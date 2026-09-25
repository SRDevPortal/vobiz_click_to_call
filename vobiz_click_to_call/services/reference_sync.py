"""Durable, post-commit updates of derived lead/patient calling summaries.

Never write a linked lead while holding the calling transaction's mapping/call
locks. The pending marker commits with the call; the scheduler recovers missed
enqueues (including Redis outages). No worker here changes call state or mapping.
"""
from __future__ import annotations

from contextlib import ExitStack
from hashlib import sha256
from uuid import uuid4
import random
from redis.exceptions import LockError

import frappe

TOKEN = "vobiz_reference_sync_token"
DUE = "vobiz_reference_sync_due"
METHOD = "vobiz_click_to_call.services.reference_sync.sync_call_references"


def request_reference_sync(call_log: str) -> None:
    if not call_log:
        return
    frappe.db.set_value("Vobiz Call Log", call_log, {
        TOKEN: uuid4().hex, DUE: frappe.utils.now_datetime(),
    }, update_modified=False)
    # Register our own callback so a Redis error AFTER commit cannot turn a
    # successful provider callback into a failed HTTP request.
    frappe.db.after_commit.add(lambda: enqueue_reference_sync(call_log))


def enqueue_reference_sync(call_log: str) -> None:
    try:
        frappe.enqueue(METHOD, call_log=call_log, queue="default", timeout=120,
                       job_id="vobiz-reference-sync-" + call_log, deduplicate=True)
    except Exception:
        # The committed marker is authoritative. A later scheduler run retries.
        # Logging itself must not break a provider response (e.g. stale log mount).
        try:
            frappe.logger().warning("Vobiz reference sync enqueue deferred: %s", call_log)
        except Exception:
            pass


def recover_pending_reference_syncs() -> None:
    pending = frappe.get_all("Vobiz Call Log",
        filters={DUE: ["<=", frappe.utils.now_datetime()]},
        pluck="name", order_by=DUE + " asc", limit_page_length=100)
    for name in pending:
        enqueue_reference_sync(name)


def _references(doc):
    return sorted({(dt, name) for dt, name in (
        (doc.reference_doctype, doc.reference_name),
        ("CRM Lead", doc.crm_lead), ("Patient", doc.patient),
    ) if dt and name})


def _update_summaries(doc) -> None:
    from vobiz_click_to_call.services.disposition import update_reference_call_metrics
    # Use the raising implementation: the public wrapper swallows failures,
    # which would otherwise acknowledge an unsuccessful background update.
    from vobiz_ai.api.processing import _sync_linked_summaries

    for dt, name in _references(doc):
        if not frappe.db.exists(dt, name):
            continue
        if (dt, name) == (doc.reference_doctype, doc.reference_name):
            update_reference_call_metrics(dt, name)
        link_field = {"CRM Lead": "crm_lead", "Patient": "patient"}.get(dt)
        if not link_field:
            continue
        # Replays of old callbacks must not replace newer-call summaries.
        latest = frappe.get_all("Vobiz Call Log", filters={link_field: name},
            pluck="name", order_by="creation desc, name desc", limit_page_length=1)
        if not latest:
            continue
        summary = frappe.get_doc("Vobiz Call Log", latest[0])
        # Update this target only; another linked target may have a newer call.
        summary.crm_lead = name if dt == "CRM Lead" else None
        summary.patient = name if dt == "Patient" else None
        _sync_linked_summaries(summary)


def _acknowledge(call_log, token):
    frappe.db.sql(
        f"UPDATE `tabVobiz Call Log` SET `{TOKEN}`=NULL, `{DUE}`=NULL "
        f"WHERE name=%s AND `{TOKEN}`=%s", (call_log, token))
    frappe.db.commit()


def _retry_later(call_log, token, jitter=False):
    frappe.db.sql(
        f"UPDATE `tabVobiz Call Log` SET `{DUE}`=%s "
        f"WHERE name=%s AND `{TOKEN}`=%s",
        (frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=120 + (random.randint(0, 30) if jitter else 0)), call_log, token))
    frappe.db.commit()


def sync_call_references(call_log: str) -> None:
    token = None
    old_timeout = frappe.db.sql("SELECT @@SESSION.innodb_lock_wait_timeout")[0][0]
    try:
        # A blocked lead must not occupy a shared worker for the DB's full timeout.
        frappe.db.sql("SET SESSION innodb_lock_wait_timeout=5")
        doc = frappe.get_doc("Vobiz Call Log", call_log)
        token = doc.get(TOKEN)
        if not token:
            return
        due = doc.get(DUE)
        if due and frappe.utils.get_datetime(due) > frappe.utils.now_datetime():
            return
        with ExitStack() as stack:
            for dt, name in _references(doc):
                key = sha256((dt + ":" + name).encode()).hexdigest()
                stack.enter_context(frappe.cache().lock(
                    "vobiz-reference-sync:" + key, timeout=110, blocking_timeout=0))
            # Drop the pre-lock read snapshot; never acquire a call/mapping row lock.
            frappe.db.rollback()
            doc = frappe.get_doc("Vobiz Call Log", call_log)
            token = doc.get(TOKEN)
            if not token:
                return
            _update_summaries(doc)
            # Release ALL lead locks before acknowledging the call's marker.
            # This prevents a lead -> call / call -> lead deadlock.
            frappe.db.commit()
            _acknowledge(call_log, token)
    except frappe.DoesNotExistError:
        frappe.db.rollback()
    except Exception as exc:
        expected_contention = isinstance(exc, (LockError, frappe.QueryTimeoutError, frappe.QueryDeadlockError))
        error = frappe.get_traceback()
        frappe.db.rollback()
        if token:
            try:
                _retry_later(call_log, token, jitter=expected_contention)
            except Exception:
                # Preserve the original marker if even acknowledgement is blocked.
                frappe.db.rollback()
        try:
            if expected_contention:
                # Site-scoped counter; first and every eighth contention per call
                # remains visible without writing a DB traceback for every retry.
                cache = frappe.cache()
                key = cache.make_key("vobiz:reference-deferrals:" + sha256(call_log.encode()).hexdigest())
                pipe = cache.pipeline(transaction=True)
                pipe.incr(key)
                pipe.expire(key, 3600)
                count, _ = pipe.execute()
                if count != 1 and count % 8:
                    return
            frappe.log_error(title="Vobiz reference sync deferred",
                             message=f"Call: {call_log}\n{error}")
        except Exception:
            pass
    finally:
        frappe.db.sql("SET SESSION innodb_lock_wait_timeout=%s", (old_timeout,))
