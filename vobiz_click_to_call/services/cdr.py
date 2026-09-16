from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe import _

from vobiz_ai.api.call_log import make_outbound_call_key, sync_linked_summaries, sync_reference_links
from vobiz_click_to_call.services.client import ProviderTemporaryError, VobizClient
from vobiz_click_to_call.services.call_status import status_from_provider
from vobiz_click_to_call.services.disposition import update_reference_call_metrics
from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.settings import get_settings

CDR_BATCH_SIZE = 5
TERMINAL_STATUSES = ("Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled")
CDR_PROVIDER_ID_FIELDS = ("call_uuid", "recording_call_uuid", "request_uuid", "a_leg_uuid", "b_leg_uuid")
STALE_RINGING_TIMEOUT_SECONDS = 60
STALE_RINGING_STATUSES = ("Queued", "Initiated", "Dialing", "Ringing", "Connecting", "Agent Ringing")
STALE_RINGING_RECOVERY_LIMIT = 1000


def recover_stale_ringing_calls() -> dict:
    """Queue guarded recovery; elapsed ringing time alone cannot prove hangup."""
    from vobiz_click_to_call.services.mapping_recovery import enqueue_pending_recovery

    return enqueue_pending_recovery()


def sync_call_log_cdr(call_log: str, ignore_permissions: bool = False, *, strict_match: bool = False) -> dict:
    settings = get_settings()
    if not settings.enabled or not settings.enable_cdr_sync:
        frappe.throw(_("Vobiz CDR Sync is disabled."))

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if not ignore_permissions and "System Manager" not in frappe.get_roles() and doc.user != frappe.session.user:
        frappe.throw(_("Not permitted."))

    # Every sync path, including the hourly batch, requires provider identity.
    # The same customer can have several unrelated calls within the search window.
    if not _provider_ids(doc):
        return {"status": "Awaiting Provider ID", "call_log": doc.name}

    original_ids = _provider_ids(doc)
    frappe.db.commit()  # Provider I/O must not retain a transaction's row locks.
    cdr = lookup_cdr(VobizClient(settings), doc)
    response = {"data": [cdr]} if cdr else {"data": []}
    # Callback updates may have arrived during the request. Lock the latest row
    # instead of saving the stale document read before the network operation.
    doc = frappe.get_doc("Vobiz Call Log", call_log, for_update=True)
    if original_ids != _provider_ids(doc) or (cdr and find_matching_cdr(doc, response) is None):
        frappe.db.rollback()
        return {"status": "Identity Changed", "call_log": call_log}
    if not cdr:
        if doc.cdr_sync_status == "Synced":
            frappe.db.commit()
            return {"status": "Synced", "call_log": doc.name}
        frappe.db.set_value("Vobiz Call Log", call_log,
                            {"cdr_sync_status": "Not Found", "cdr_synced_at": frappe.utils.now()},
                            update_modified=False)
        frappe.db.commit()
        return {"status": "Not Found", "call_log": doc.name}

    if not _terminal_cdr(cdr):
        frappe.db.commit()
        return {"status": "Provider Active", "call_log": doc.name}

    apply_cdr_to_call_log(doc, cdr, response)
    frappe.db.commit()
    return {"status": "Synced", "call_log": doc.name}


def enqueue_recent_cdr_sync(limit: int = 100, batch_size: int = CDR_BATCH_SIZE) -> dict:
    settings = get_settings()
    if not settings.enabled or not settings.enable_cdr_sync:
        return {"queued": 0, "disabled": True}

    limit = max(1, min(int(limit or 100), 200))
    batch_size = max(1, min(int(batch_size or CDR_BATCH_SIZE), CDR_BATCH_SIZE))

    from vobiz_click_to_call.services.recovery_policy import due
    filters = {"cdr_sync_status": ["in", ["", "Not Synced", "Not Found", "Failed"]],
               "status": ["in", TERMINAL_STATUSES],
               "creation": ["<", frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-60)]}
    provider_ids = [[field, "is", "set"] for field in CDR_PROVIDER_ID_FIELDS]
    # Reserve at least half the work for older/least-recently-attempted records.
    # New traffic must not permanently hide the existing backlog.
    recent = frappe.get_all("Vobiz Call Log", filters=filters, or_filters=provider_ids,
                            fields=["name"], order_by="creation desc", limit=max(1, limit // 2))
    backlog = frappe.get_all("Vobiz Call Log", filters=filters, or_filters=provider_ids,
                             fields=["name"], order_by="cdr_synced_at asc, creation asc", limit=limit)
    names = list(dict.fromkeys(row.name for row in recent + backlog if due(row.name, "cdr")))[:limit]
    for start in range(0, len(names), batch_size):
        batch = names[start : start + batch_size]
        batch_hash = hashlib.sha1("|".join(batch).encode()).hexdigest()[:16]
        frappe.enqueue(
            "vobiz_click_to_call.services.cdr.process_cdr_batch",
            queue="long",
            timeout=600,
            job_id=f"vobiz-cdr-batch-{batch_hash}",
            deduplicate=True,
            call_logs=batch,
        )

    return {"queued": len(names), "batch_size": batch_size}


def enqueue_missing_inbound_cdr_sync(days: int = 2) -> dict:
    settings = get_settings()
    if not settings.enabled or not settings.enable_cdr_sync:
        return {"queued": 0, "disabled": True}

    days = max(1, min(int(days or 2), 7))
    dates = [str(frappe.utils.add_days(frappe.utils.today(), -offset)) for offset in range(days)]
    for date in dates:
        frappe.enqueue(
            "vobiz_click_to_call.services.cdr.sync_missing_inbound_cdrs",
            queue="long",
            timeout=300,
            job_id=f"vobiz-missing-inbound-cdr-{date}",
            deduplicate=True,
            date=date,
        )
    return {"queued": len(dates), "dates": dates}


def sync_recent_cdrs(limit: int = 50) -> dict:
    result = enqueue_recent_cdr_sync(limit=limit)
    result["missing_inbound"] = enqueue_missing_inbound_cdr_sync()
    return result


def sync_missing_inbound_cdrs(date: str | None = None, limit: int = 100) -> dict:
    settings = get_settings()
    if not settings.enabled or not settings.enable_cdr_sync:
        return {"created": 0, "skipped": 0, "disabled": True}

    date = str(date or frappe.utils.today())
    limit = max(1, min(int(limit or 100), 100))
    try:
        response = VobizClient(settings).search_cdrs({"start_date": date, "end_date": date,
                                                   "call_direction": "inbound", "per_page": limit})
    except ProviderTemporaryError:
        # The next scheduled sweep retries without turning an account budget or
        # temporary provider outage into another generic scheduler traceback.
        return {"created": 0, "skipped": 0, "pending_provider": True}
    rows = extract_cdr_rows(response)
    created = 0
    skipped = 0
    names = []
    for cdr in rows[:limit]:
        if not _is_inbound_cdr(cdr):
            continue
        if _existing_call_log_for_cdr(cdr):
            skipped += 1
            continue
        doc = create_missing_inbound_call_log_from_cdr(cdr)
        if doc:
            created += 1
            names.append(doc.name)
        else:
            skipped += 1
        if (created + skipped) % CDR_BATCH_SIZE == 0:
            frappe.db.commit()

    frappe.db.commit()
    return {"created": created, "skipped": skipped, "call_logs": names}


def process_cdr_batch(call_logs: list[str] | tuple[str, ...]) -> dict:
    from vobiz_click_to_call.services import recovery_policy
    call_logs = list(call_logs or [])[:CDR_BATCH_SIZE]
    result = {"synced": 0, "not_found": 0, "failed": 0}
    for call_log in call_logs:
        try:
            with recovery_policy.attempt(call_log, "cdr") as allowed:
                if not allowed:
                    continue
                # Attempt timestamps also rotate transient failures to the back
                # of the backlog; this metadata must not extend call activity.
                frappe.db.set_value("Vobiz Call Log", call_log, "cdr_synced_at",
                                    frappe.utils.now(), update_modified=False)
                frappe.db.commit()
                status = sync_call_log_cdr(call_log, ignore_permissions=True).get("status")
                if status == "Synced":
                    result["synced"] += 1
                    recovery_policy.clear(call_log, "cdr")
                elif status == "Not Found":
                    result["not_found"] += 1
        except Exception:
            frappe.db.rollback()
            result["failed"] += 1
            frappe.log_error(frappe.get_traceback(), "Vobiz CDR sync failed")

    return result


def create_missing_inbound_call_log_from_cdr(cdr: dict, raw_response: dict | None = None):
    uuid = str(cdr.get("uuid") or cdr.get("call_uuid") or "").strip()
    sip_call_id = str(cdr.get("sip_call_id") or "").strip()
    if not uuid and not sip_call_id:
        return None

    customer_number = _normalize_cdr_phone(
        cdr.get("caller_id_number") or cdr.get("from") or cdr.get("source_number"),
    )
    did_number = _normalize_cdr_phone(
        cdr.get("destination_number") or cdr.get("to") or cdr.get("callee_id_number"),
    )
    if not customer_number:
        return None

    previous = _last_outbound_for_customer(customer_number)
    if not previous:
        return None

    doc = frappe.get_doc(
        {
            "doctype": "Vobiz Call Log",
            "call_key": make_outbound_call_key(),
            "source_app": "vobiz_click_to_call",
            "reference_doctype": previous.reference_doctype,
            "reference_name": previous.reference_name,
            "phone_field": previous.phone_field,
            "user": previous.user,
            "user_mobile": previous.user_mobile,
            "agent_number": previous.user_mobile or previous.agent_number,
            "customer_number": customer_number,
            "normalized_customer_number": customer_number,
            "caller_id": did_number or previous.caller_id,
            "did_number": did_number or previous.did_number or previous.caller_id,
            "normalized_did": normalize_phone_number(did_number or previous.did_number or previous.caller_id, default_country_code=_default_country_code()),
            "call_flow": "Customer First",
            "direction": "Incoming",
            "status": status_from_cdr(cdr, "No Answer"),
            "call_status": cdr.get("status") or cdr.get("call_status") or cdr.get("hangup_cause"),
            "hangup_cause": cdr.get("hangup_cause") or cdr.get("hangup_cause_name"),
            "from_number": customer_number,
            "to_number": did_number,
            "start_time": _cdr_datetime(cdr.get("start_time") or cdr.get("created_at")),
            "answer_time": _cdr_datetime(cdr.get("answer_time")),
            "end_time": _cdr_datetime(cdr.get("end_time") or cdr.get("updated_at")),
            "creation": _cdr_datetime(cdr.get("created_at") or cdr.get("start_time")),
            "modified": _cdr_datetime(cdr.get("updated_at") or cdr.get("end_time")),
            "duration": first_int(cdr, "duration", "call_duration"),
            "billsec": first_int(cdr, "billsec", "bill_seconds", "billed_duration"),
            "ring_time": first_int(cdr, "ring_time"),
            "cost": first_float(cdr, "cost", "total_cost", "total_amount", "charge"),
            "currency": cdr.get("currency") or "INR",
            "call_uuid": uuid,
            "sip_call_id": sip_call_id,
            "cdr_sync_status": "Synced",
            "cdr_synced_at": frappe.utils.now(),
            "cdr_json": json.dumps({"matched_cdr": cdr, "raw_response": raw_response or {}}, indent=2, default=str),
            "request_json": json.dumps({"source": "missing_inbound_cdr_import", "cdr": cdr}, indent=2, default=str),
            "recording_status": "Not Started",
            "transcript_status": "Not Requested",
            "ai_status": "Pending",
            "ai_disposition_status": "Not Requested",
            "error_message": "Recovered from Vobiz inbound CDR because inbound webhook did not create an ERP call log.",
        }
    )
    doc.crm_lead = previous.crm_lead
    doc.patient = previous.patient
    sync_reference_links(doc)
    doc.insert(ignore_permissions=True)
    update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
    sync_linked_summaries(doc)
    return doc


def _is_inbound_cdr(cdr: dict) -> bool:
    direction = str(cdr.get("call_direction") or cdr.get("direction") or "").strip().lower()
    return direction == "inbound"


def _existing_call_log_for_cdr(cdr: dict) -> str:
    meta = frappe.get_meta("Vobiz Call Log")
    for fieldname, value in (
        ("call_uuid", cdr.get("uuid") or cdr.get("call_uuid")),
        ("sip_call_id", cdr.get("sip_call_id")),
    ):
        if not value or not meta.has_field(fieldname):
            continue
        name = frappe.db.get_value("Vobiz Call Log", {fieldname: value}, "name")
        if name:
            return name
    return ""


def _last_outbound_for_customer(customer_number: str):
    filters = {
        "source_app": "vobiz_click_to_call",
        "direction": "Outgoing",
        "user": ["is", "set"],
        "user_mobile": ["is", "set"],
    }
    fields = [
        "name",
        "user",
        "user_mobile",
        "agent_number",
        "caller_id",
        "did_number",
        "reference_doctype",
        "reference_name",
        "phone_field",
        "crm_lead",
        "patient",
    ]
    for fieldname, value in (("normalized_customer_number", customer_number), ("customer_number", customer_number)):
        if not value:
            continue
        rows = frappe.get_all("Vobiz Call Log", filters={**filters, fieldname: value}, fields=fields, order_by="creation desc", limit=1)
        if rows:
            return rows[0]
    return None


def _cdr_datetime(value):
    if not value:
        return None
    try:
        parsed = frappe.utils.get_datetime(value)
        return parsed.replace(tzinfo=None) if getattr(parsed, "tzinfo", None) else parsed
    except Exception:
        return value


def _default_country_code() -> str:
    return str(getattr(get_settings(), "default_country_code", None) or "+91")


def _normalize_cdr_phone(value: str | None) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    default_country = _default_country_code()
    default_digits = "".join(ch for ch in default_country if ch.isdigit())
    if text.startswith("0") and default_digits and len(digits) == 11:
        return normalize_phone_number(digits[1:], default_country_code=default_country)
    return normalize_phone_number(text, default_country_code=default_country)


def build_cdr_search_params(doc) -> dict[str, Any]:
    params: dict[str, Any] = {"per_page": 50}
    lookup_uuid = doc.get("call_uuid") or doc.get("recording_call_uuid") or doc.get("request_uuid")
    if lookup_uuid:
        params["search"] = lookup_uuid
    if doc.get("customer_number"):
        key = "from_number" if doc.get("direction") == "Incoming" else "to_number"
        params[key] = str(doc.get("customer_number")).lstrip("+")
    # A browser endpoint username is not an originating phone number.
    if doc.get("creation"):
        created = frappe.utils.getdate(doc.get("creation"))
        params["start_date"] = str(frappe.utils.add_days(created, -1))
        params["end_date"] = str(frappe.utils.add_days(created, 1))
    return params


def lookup_cdr(client, doc):
    """Prefer one exact lookup; bounded searches must still match provider IDs."""
    if not _provider_ids(doc):
        return None
    call_id = doc.get("call_uuid") or doc.get("recording_call_uuid")
    if call_id:
        response = client.retrieve_cdr(str(call_id))
        if isinstance(response, dict):
            data = response.get("data", response)
            single = {"data": [data]} if isinstance(data, dict) else response
            found = find_matching_cdr(doc, single)
            if found:
                return found
    params = build_cdr_search_params(doc)
    for page in (1, 2):
        response = client.search_cdrs(dict(params, page=page))
        found = find_matching_cdr(doc, response)
        if found:
            return found
        if not (response.get("pagination") or {}).get("has_next"):
            break
    return None


def _provider_ids(doc) -> set[str]:
    return {str(doc.get(key)).strip() for key in CDR_PROVIDER_ID_FIELDS
            if doc.get(key) and str(doc.get(key)).strip()}


def find_matching_cdr(doc, response: dict, *, strict_match: bool = False) -> dict | None:
    candidates = extract_cdr_rows(response)
    if not candidates:
        return None

    provider_ids = _provider_ids(doc)
    if not provider_ids:
        return None
    for cdr in candidates:
        values = {str(cdr[key]).strip() for key in (
            "uuid", "call_uuid", "CallUUID", "request_uuid", "request_id",
            "a_leg_uuid", "b_leg_uuid", "ALegUUID", "BLegUUID",
        ) if cdr.get(key)}
        if provider_ids.intersection(values):
            return cdr

    # Phone numbers, timestamps and browser-supplied event UUIDs alone are not
    # trusted call identity. Do not fall back to a different call for that number.
    return None


def _terminal_cdr(cdr: dict) -> bool:
    parent_state = str(cdr.get("status") or cdr.get("call_status") or "").lower().replace("_", "-")
    if parent_state in {"in-progress", "live", "ringing", "answered", "connected", "queued"}:
        return False
    if cdr.get("end_time") or cdr.get("EndTime"):
        return True
    states = {str(cdr.get(key) or "").lower().replace("_", "-") for key in (
        "status", "call_status", "dial_status", "b_leg_status",
    )}
    return bool(states.intersection({
        "completed", "hangup", "ended", "failed", "busy", "no-answer", "no answer",
        "cancelled", "canceled", "timeout",
    }))


def extract_cdr_rows(response: dict) -> list[dict]:
    data = response.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("cdrs", "objects", "results"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    for key in ("cdrs", "objects", "results"):
        rows = response.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def apply_cdr_to_call_log(doc, cdr: dict, raw_response: dict) -> None:
    if find_matching_cdr(doc, {"data": [cdr]}) is None:
        frappe.throw(_("CDR does not match this call's provider identifiers."))
    doc.cdr_json = _bounded_json({"matched_cdr": cdr, "raw_response": raw_response})
    doc.cdr_sync_status = "Synced"
    doc.cdr_synced_at = frappe.utils.now()

    doc.duration = first_int(cdr, "duration", "call_duration", fallback=doc.duration)
    doc.billsec = first_int(cdr, "billsec", "bill_seconds", "billed_duration", fallback=doc.billsec)
    doc.cost = first_float(cdr, "cost", "total_amount", "charge", fallback=doc.cost)
    doc.currency = cdr.get("currency") or doc.currency or "INR"
    # An A-leg CDR can carry billsec/NORMAL_CLEARING when the customer leg
    # failed. Enrich final calls without replacing their callback outcome.
    if doc.status not in TERMINAL_STATUSES:
        doc.hangup_cause = cdr.get("hangup_cause") or cdr.get("hangup_cause_name") or doc.hangup_cause
        doc.call_status = cdr.get("status") or cdr.get("call_status") or doc.call_status
    recording_url = cdr.get("recording_url") or cdr.get("record_url") or doc.recording_url
    doc.recording_url = recording_url
    if recording_url and doc.recording_status != "Completed":
        doc.recording_status = "Completed"
    if doc.status not in TERMINAL_STATUSES:
        doc.status = status_from_cdr(dict(cdr, dial_status=cdr.get("dial_status")
                                        or cdr.get("b_leg_status") or doc.get("dial_status")), doc.status)
    doc.save(ignore_permissions=True)
    update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
    sync_linked_summaries(doc)


def status_from_cdr(cdr: dict, current_status: str) -> str:
    return status_from_provider(
        {
            "status": cdr.get("status"),
            "call_status": cdr.get("call_status") or cdr.get("status"),
            "dial_status": cdr.get("dial_status") or cdr.get("b_leg_status"),
            "hangup_cause": cdr.get("hangup_cause") or cdr.get("hangup_cause_name"),
            "duration": first_int(cdr, "duration", "call_duration"),
            "billsec": first_int(cdr, "billsec", "bill_seconds", "billed_duration"),
        },
        previous=current_status,
    ) or current_status or "Completed"


def first_int(row: dict, *keys: str, fallback=None) -> int:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            try:
                return int(float(value))
            except Exception:
                pass
    return fallback or 0


def first_float(row: dict, *keys: str, fallback=None) -> float:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except Exception:
                pass
    return fallback or 0.0


def _bounded_json(value: Any, max_chars: int = 64 * 1024) -> str:
    encoded = json.dumps(value, indent=2, default=str)
    if len(encoded) <= max_chars:
        return encoded
    return json.dumps(
        {
            "truncated": True,
            "original_size": len(encoded),
            "payload_preview": encoded[:max_chars],
        },
        indent=2,
    )
