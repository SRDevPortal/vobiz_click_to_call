from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe import _

from vobiz_ai.api.call_log import make_outbound_call_key, sync_linked_summaries, sync_reference_links
from vobiz_click_to_call.services.client import VobizClient
from vobiz_click_to_call.services.call_status import status_from_provider
from vobiz_click_to_call.services.disposition import update_reference_call_metrics
from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.settings import get_settings

CDR_BATCH_SIZE = 10


def sync_call_log_cdr(call_log: str, ignore_permissions: bool = False) -> dict:
    settings = get_settings()
    if not settings.enabled or not settings.enable_cdr_sync:
        frappe.throw(_("Vobiz CDR Sync is disabled."))

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if not ignore_permissions and "System Manager" not in frappe.get_roles() and doc.user != frappe.session.user:
        frappe.throw(_("Not permitted."))

    params = build_cdr_search_params(doc)
    response = VobizClient(settings).search_cdrs(params)
    cdr = find_matching_cdr(doc, response)
    if not cdr:
        doc.cdr_sync_status = "Not Found"
        doc.cdr_synced_at = frappe.utils.now()
        doc.cdr_json = _bounded_json(response)
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {"status": "Not Found", "call_log": doc.name}

    apply_cdr_to_call_log(doc, cdr, response)
    frappe.db.commit()
    return {"status": "Synced", "call_log": doc.name}


def enqueue_recent_cdr_sync(limit: int = 50, batch_size: int = CDR_BATCH_SIZE) -> dict:
    settings = get_settings()
    if not settings.enabled or not settings.enable_cdr_sync:
        return {"queued": 0, "disabled": True}

    limit = max(1, min(int(limit or 50), 200))
    batch_size = max(1, min(int(batch_size or CDR_BATCH_SIZE), CDR_BATCH_SIZE))

    rows = frappe.get_all(
        "Vobiz Call Log",
        filters={"cdr_sync_status": ["in", ["", "Not Synced", "Not Found", "Failed"]]},
        fields=["name"],
        order_by="creation desc",
        limit=limit,
    )

    names = [row.name for row in rows]
    for start in range(0, len(names), batch_size):
        batch = names[start : start + batch_size]
        batch_hash = hashlib.sha1("|".join(batch).encode()).hexdigest()[:16]
        frappe.enqueue(
            "vobiz_click_to_call.services.cdr.process_cdr_batch",
            queue="long",
            timeout=300,
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
    response = VobizClient(settings).search_cdrs({"date": date})
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
    call_logs = list(call_logs or [])[:CDR_BATCH_SIZE]
    result = {"synced": 0, "not_found": 0, "failed": 0}
    for call_log in call_logs:
        try:
            status = sync_call_log_cdr(call_log, ignore_permissions=True).get("status")
            if status == "Synced":
                result["synced"] += 1
            elif status == "Not Found":
                result["not_found"] += 1
        except Exception:
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
    params: dict[str, Any] = {}
    if doc.call_uuid:
        params["call_uuid"] = doc.call_uuid
    if doc.request_uuid:
        params["request_uuid"] = doc.request_uuid
    if doc.customer_number:
        params["to"] = doc.customer_number
    if doc.caller_id:
        params["from"] = doc.caller_id
    if doc.creation:
        created = frappe.utils.getdate(doc.creation)
        params["start_date"] = str(frappe.utils.add_days(created, -1))
        params["end_date"] = str(frappe.utils.add_days(created, 1))
    return params


def find_matching_cdr(doc, response: dict) -> dict | None:
    candidates = extract_cdr_rows(response)
    if not candidates:
        return None

    provider_ids = {value for value in (doc.call_uuid, doc.request_uuid, doc.a_leg_uuid, doc.b_leg_uuid) if value}
    for cdr in candidates:
        values = {str(value) for value in cdr.values() if value}
        if provider_ids.intersection(values):
            return cdr

    if doc.customer_number:
        customer = "".join(ch for ch in doc.customer_number if ch.isdigit())
        for cdr in candidates:
            numbers = [
                cdr.get("to"),
                cdr.get("from"),
                cdr.get("destination_number"),
                cdr.get("caller_id_number"),
                cdr.get("callee_id_number"),
            ]
            if any(customer and customer in "".join(ch for ch in str(number or "") if ch.isdigit()) for number in numbers):
                return cdr

    return None


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
    doc.cdr_json = _bounded_json({"matched_cdr": cdr, "raw_response": raw_response})
    doc.cdr_sync_status = "Synced"
    doc.cdr_synced_at = frappe.utils.now()

    doc.duration = first_int(cdr, "duration", "call_duration", fallback=doc.duration)
    doc.billsec = first_int(cdr, "billsec", "bill_seconds", "billed_duration", fallback=doc.billsec)
    doc.cost = first_float(cdr, "cost", "total_amount", "charge", fallback=doc.cost)
    doc.currency = cdr.get("currency") or doc.currency or "INR"
    doc.hangup_cause = cdr.get("hangup_cause") or cdr.get("hangup_cause_name") or doc.hangup_cause
    doc.call_status = cdr.get("status") or cdr.get("call_status") or doc.call_status
    doc.recording_url = cdr.get("recording_url") or cdr.get("record_url") or doc.recording_url
    doc.status = status_from_cdr(cdr, doc.status)
    doc.save(ignore_permissions=True)
    update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
    sync_linked_summaries(doc)


def status_from_cdr(cdr: dict, current_status: str) -> str:
    return status_from_provider(
        {
            "status": cdr.get("status"),
            "call_status": cdr.get("call_status"),
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
