"""Manual, resumable transcript recovery. No call lifecycle or AI hooks."""
from contextlib import contextmanager
from datetime import timedelta
from time import monotonic
import hashlib

import frappe
from frappe.utils import cint, getdate, get_datetime, now_datetime
from vobiz_click_to_call.services.client import ProviderTemporaryError
from vobiz_click_to_call.services.settings import get_auth_credentials, get_settings
from vobiz_click_to_call.services.transcript_provider import TranscriptReader, TranscriptLookupError, TranscriptAccessError

RUN = "Vobiz Transcript Sync"
RESULT = "Vobiz Transcript Sync Result"
CALL = "Vobiz Call Log"
ACTIVE = ("Queued", "Running")
TERMINAL = {"completed", "failed", "busy", "no answer", "cancelled", "canceled"}
IDENTITY = ("account_id", "call_uuid", "recording_call_uuid", "recording_id", "a_leg_uuid", "b_leg_uuid")
FIELDS = ["name", "creation", "status", "transcript_text", "transcription_text", "transcription_id", *IDENTITY]
COUNTERS = {"Updated": "updated", "Skipped": "skipped", "Unavailable": "unavailable", "Failed": "failed"}


def validate_options(from_date, to_date, batch_size, max_call_logs=1000):
    if not from_date or not to_date:
        frappe.throw("Select both dates.")
    start, end = getdate(from_date), getdate(to_date)
    if end < start or (end - start).days > 30:
        frappe.throw("Choose a date range of 1–31 days.")
    if not str(batch_size).isdigit() or not 1 <= int(batch_size) <= 50:
        frappe.throw("Calls per batch must be a whole number from 1 to 50.")
    if not str(max_call_logs).isdigit() or not 1 <= int(max_call_logs) <= 100000:
        frappe.throw("Maximum call logs must be a whole number from 1 to 100,000.")
    return start, end, int(batch_size)


def total_limit(doc):
    # Compatibility with runs created before the limit field was added.
    return cint(doc.get("max_call_logs") if doc.get("max_call_logs") is not None else 1000)


def remaining(doc):
    return max(0, total_limit(doc) - cint(doc.get("processed")))


def progress_values(run, candidate, outcome, reason):
    processed = cint(run.processed) + 1
    complete = processed >= total_limit(run)
    return {
        "status": "Completed" if complete else "Running",
        "cursor_time": candidate.creation, "cursor_name": candidate.name,
        "processed": processed, COUNTERS[outcome]: cint(run.get(COUNTERS[outcome])) + 1,
        "retry_count": 0, "next_attempt": None,
        "last_message": f"Maximum call logs reached ({processed}). " + reason if complete else reason,
    }


def require_manager():
    frappe.only_for("System Manager")


@contextmanager
def lock(key, timeout=240):
    mutex = frappe.cache.lock("vobiz-transcript-sync:" + key, timeout=timeout)
    acquired = mutex.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            mutex.release()


def enqueue(name):
    frappe.enqueue("vobiz_click_to_call.services.transcript_sync.run_batch",
                   name=name, queue="long", timeout=180, job_id="transcript-sync:" + name,
                   deduplicate=True, enqueue_after_commit=True)


def ensure_no_other_run(name):
    others = frappe.get_all(RUN, filters={"status": ["in", ACTIVE], "name": ["!=", name]},
                            pluck="name", limit_page_length=1)
    if others:
        frappe.throw("Another transcript sync is active. Pause or finish it first.")


@frappe.whitelist(methods=["POST"])
def start(name):
    require_manager()
    with lock("start", timeout=30) as acquired:
        if not acquired:
            frappe.throw("Another sync is starting. Please retry.")
        doc = frappe.get_doc(RUN, name)
        doc.check_permission("write")
        validate_options(doc.from_date, doc.to_date, doc.batch_size, total_limit(doc))
        if doc.status == "Completed":
            frappe.throw("This run is complete. Create a new run to check unavailable transcripts again.")
        ensure_no_other_run(name)
        auth_id, token = get_auth_credentials()
        if not auth_id or not token:
            frappe.throw("Configure Vobiz Auth ID and Auth Token first.")
        if doc.account_id and doc.account_id != auth_id:
            frappe.throw("The Vobiz account has changed. Create a new sync.")
        if doc.status in ACTIVE:
            return {"name": name, "status": doc.status}
        values = {"status": "Queued", "next_attempt": None, "retry_count": 0,
                  "last_message": "Queued for transcript recovery."}
        if doc.status == "Draft":
            values.update(account_id=auth_id, requested_by=frappe.session.user, scan_until=now_datetime())
        else:
            # The resuming manager becomes the accountable operator.
            values["requested_by"] = frappe.session.user
        frappe.db.set_value(RUN, name, values)
        frappe.db.commit()
    enqueue(name)
    return {"name": name, "status": "Queued"}


@frappe.whitelist(methods=["POST"])
def pause(name):
    require_manager()
    doc = frappe.get_doc(RUN, name)
    doc.check_permission("write")
    if doc.status in ACTIVE:
        frappe.db.set_value(RUN, name, {"status": "Paused", "last_message": "Paused. Resume to continue."})
    return {"name": name}


def candidates(doc, limit):
    start, end, _ = validate_options(doc.from_date, doc.to_date, doc.batch_size, total_limit(doc))
    limit = min(int(limit), remaining(doc))
    if limit <= 0:
        return []
    params = [str(start), str(end + timedelta(days=1)), doc.scan_until or now_datetime()]
    cursor = ""
    if doc.cursor_time:
        cursor = "AND (creation > %s OR (creation = %s AND name > %s))"
        params.extend([doc.cursor_time, doc.cursor_time, doc.cursor_name or ""])
    params.append(int(limit))
    # Existing idx_vobiz_call_creation serves this keyset scan. Never COUNT the
    # backlog or OFFSET through millions of records.
    return frappe.db.sql(
        f"""SELECT name, creation FROM `tabVobiz Call Log`
            WHERE creation >= %s AND creation < %s AND creation <= %s {cursor}
            ORDER BY creation, name LIMIT %s""", params, as_dict=True)


def eligibility(doc, account):
    if (doc.get("transcript_text") or "").strip() or (doc.get("transcription_text") or "").strip():
        return "Transcript already present."
    if str(doc.get("status") or "").strip().lower() not in TERMINAL:
        return "Call is not in a final status."
    if doc.get("account_id") and doc.account_id != account:
        return "Call belongs to a different Vobiz account."
    if not any(doc.get(k) for k in ("call_uuid", "recording_call_uuid", "a_leg_uuid", "b_leg_uuid")):
        return "No trusted provider call UUID."
    return ""


@frappe.whitelist()
def preview(name):
    require_manager()
    doc = frappe.get_doc(RUN, name)
    doc.check_permission("read")
    auth_id, _ = get_auth_credentials()
    rows = candidates(doc, 100)
    eligible = 0
    for row in rows:
        call = frappe.db.get_value(CALL, row.name, FIELDS, as_dict=True)
        eligible += int(bool(call) and not eligibility(call, auth_id))
    return {"checked": len(rows), "eligible": eligible, "sample_limit": 100,
            "note": "Preview of the next 100 call logs, not the full backlog count. No provider requests were made."}


def schedule():
    if not frappe.db.exists("DocType", RUN):
        return
    for name in frappe.get_all(RUN, filters={"status": ["in", ACTIVE]}, pluck="name", limit_page_length=10):
        enqueue(name)


def active_run(name, *, for_update=False):
    doc = frappe.get_doc(RUN, name, for_update=for_update)
    return doc if doc.status in ACTIVE else None


def apply_transcript(snapshot, payload):
    """Called only after network I/O. Lock/recheck the current call, then update
    the transcript allowlist directly so doc hooks cannot launch AI/routing."""
    current = frappe.db.get_value(CALL, snapshot.name, FIELDS, as_dict=True, for_update=True)
    if not current:
        return "Skipped", "Call log was removed."
    reason = eligibility(current, payload["account_id"])
    if reason:
        return "Skipped", reason
    if any((current.get(k) or "") != (snapshot.get(k) or "") for k in IDENTITY):
        return "Failed", "Call identity changed during lookup; retry in a new run."
    ids = {current.get(k) for k in ("call_uuid", "recording_call_uuid", "a_leg_uuid", "b_leg_uuid")}
    if payload["call_uuid"] not in ids:
        return "Failed", "Provider identity does not match this call."
    if current.transcription_id and payload["transcription_id"] and current.transcription_id != payload["transcription_id"]:
        return "Failed", "Existing transcription ID conflicts with provider response."
    values = {
        "transcript_text": payload["text"], "transcription_text": payload["text"],
        "transcript_status": "Completed", "transcript_received_at": now_datetime(),
        "transcript_error": "",
    }
    if not current.transcription_id and payload["transcription_id"]:
        values["transcription_id"] = payload["transcription_id"]
    frappe.db.set_value(CALL, current.name, values, update_modified=False)
    return "Updated", "Existing provider transcript recovered; manual disposition unchanged."


def checkpoint(run, candidate, outcome, reason):
    # Audit entry + call change + cursor are committed together.
    result_name = hashlib.sha256((run.name + ":" + candidate.name).encode()).hexdigest()[:32]
    frappe.get_doc(dict(doctype=RESULT, name=result_name, sync_run=run.name,
                        call_log=candidate.name, outcome=outcome, reason=reason)).insert(ignore_permissions=True)
    frappe.db.set_value(RUN, run.name, progress_values(run, candidate, outcome, reason))
    frappe.db.commit()


def retry_or_fail(name, candidate, error):
    run = active_run(name, for_update=True)
    if not run:
        frappe.db.rollback()
        return
    attempts = cint(run.retry_count) + 1
    local_budget = error.source == "local_budget"
    if attempts >= 3 and not local_budget:
        checkpoint(run, candidate, "Failed", "Temporary provider errors persisted after 3 attempts.")
        return
    delay = max(60, min(3600, error.retry_after or 60 * (2 ** min(attempts - 1, 5))))
    frappe.db.set_value(RUN, name, {
        "retry_count": run.retry_count if local_budget else attempts,
        "next_attempt": now_datetime() + timedelta(seconds=delay),
        "last_message": "Waiting before retrying the same call; progress is saved.",
    })
    frappe.db.commit()


def run_batch(name):
    with lock(name) as acquired:
        if not acquired:
            return
        old_user = frappe.session.user
        old_wait = frappe.db.sql("SELECT @@SESSION.innodb_lock_wait_timeout")[0][0]
        try:
            frappe.db.sql("SET SESSION innodb_lock_wait_timeout = 3")
            run = active_run(name)
            if not run or (run.next_attempt and get_datetime(run.next_attempt) > now_datetime()):
                return
            if not frappe.db.get_value("User", run.requested_by, "enabled"):
                raise TranscriptLookupError("The initiating user is disabled.")
            frappe.set_user(run.requested_by)
            require_manager()
            reader = TranscriptReader()
            if reader.auth_id != run.account_id or not reader.auth_token:
                raise TranscriptLookupError("Vobiz credentials/account changed; review settings.")
            rows = candidates(run, run.batch_size)
            frappe.db.commit()  # never retain a transaction across provider I/O
            if not rows:
                latest = active_run(name, for_update=True)
                if latest:
                    frappe.db.set_value(RUN, name, {"status": "Completed", "last_message": "Maximum call logs reached." if not remaining(latest) else "Selected date range checked."})
                    frappe.db.commit()
                return
            started = monotonic()
            for candidate in rows:
                run = active_run(name)
                if not run:
                    return
                snapshot = frappe.db.get_value(CALL, candidate.name, FIELDS, as_dict=True)
                reason = eligibility(snapshot, run.account_id) if snapshot else "Call log was removed."
                frappe.db.commit()
                payload = None
                outcome = "Skipped"
                if not reason:
                    try:
                        payload = reader.lookup(snapshot)
                        outcome, reason = ("Unavailable", "Vobiz has no retrievable transcript text for this call.")
                    except ProviderTemporaryError as error:
                        retry_or_fail(name, candidate, error)
                        return
                    except TranscriptAccessError:
                        raise
                    except TranscriptLookupError as error:
                        outcome, reason = "Failed", str(error)
                # Pause can be requested while HTTP is running. Recheck under a
                # short lock; do not apply that in-flight response after pausing.
                run = active_run(name, for_update=True)
                if not run:
                    frappe.db.rollback()
                    return
                if payload:
                    outcome, reason = apply_transcript(snapshot, payload)
                if snapshot:
                    checkpoint(run, candidate, outcome, reason)
                else:
                    # Missing call cannot be a Link in the audit table.
                    frappe.db.set_value(RUN, name, progress_values(run, candidate, "Skipped", reason))
                    frappe.db.commit()
                if monotonic() - started >= 45:
                    break
            # Next minute's scheduler tick continues this run. No chained flood.
        except Exception:
            frappe.db.rollback()
            current = active_run(name, for_update=True)
            if current:
                frappe.db.set_value(RUN, name, {"status": "Failed",
                    "last_message": "Batch stopped. Review Error Log, then Resume; completed calls remain saved."})
                frappe.db.commit()
            frappe.log_error(title="Vobiz transcript sync failed")
            raise
        finally:
            frappe.db.rollback()
            frappe.db.sql("SET SESSION innodb_lock_wait_timeout = %s", (old_wait,))
            frappe.set_user(old_user)
