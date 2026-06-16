from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _

from vobiz_ai.api.call_log import sync_linked_summaries
from vobiz_click_to_call.services.client import VobizClient
from vobiz_click_to_call.services.call_status import status_from_provider
from vobiz_click_to_call.services.disposition import update_reference_call_metrics
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
        doc.cdr_json = json.dumps(response, indent=2, default=str)
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

    limit = max(1, int(limit or 50))
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
        frappe.enqueue(
            "vobiz_click_to_call.services.cdr.process_cdr_batch",
            queue="short",
            timeout=300,
            call_logs=names[start : start + batch_size],
        )

    return {"queued": len(names), "batch_size": batch_size}


def sync_recent_cdrs(limit: int = 50) -> dict:
    return enqueue_recent_cdr_sync(limit=limit)


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
    doc.cdr_json = json.dumps({"matched_cdr": cdr, "raw_response": raw_response}, indent=2, default=str)
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
