"""Retry early cancellations once an authenticated provider call ID arrives."""
import json

import frappe

TERMINAL = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}


def cancellation_requested(doc):
    try:
        data = json.loads(doc.get("response_json") or "{}")
        return isinstance(data, dict) and data.get("cancel_requested") is True
    except (ValueError, TypeError, AttributeError):
        return False


def queue_pending_cancel(doc, method=None):
    if not cancellation_requested(doc) or doc.status in TERMINAL:
        return
    if not (doc.call_uuid or doc.a_leg_uuid or doc.b_leg_uuid):
        return
    try:
        frappe.enqueue("vobiz_click_to_call.services.cancellation.cancel_pending",
                       call_log=doc.name, queue="short", timeout=120, enqueue_after_commit=True,
                       job_id="vctc-cancel-" + doc.name, deduplicate=True)
    except Exception:
        # Preserve the call update and intent; the scheduler retries later.
        frappe.log_error(title="Vobiz cancellation queue unavailable", message=frappe.get_traceback())


def cancel_pending(call_log):
    from vobiz_click_to_call.services.client import VobizClient
    doc = frappe.get_doc("Vobiz Call Log", call_log, for_update=True)
    uuid = doc.call_uuid or doc.a_leg_uuid or doc.b_leg_uuid
    pending = cancellation_requested(doc) and doc.status not in TERMINAL
    frappe.db.commit()  # Never hold a call-row lock during provider I/O.
    if pending and uuid:
        client = VobizClient()
        client.timeout = 3
        return client.hangup_call(uuid, allow_missing=True)
    # Only a terminal callback/CDR may release the reservation.


def recover_pending_cancellations():
    # Keyset scan avoids starving older calls behind a fixed first page.
    # Provider callbacks replace call_status (for example with "answered").
    # Cancellation intent lives in response_json and must survive those updates.
    cache = frappe.cache()
    cursor = cache.get_value("vctc:cancel-cursor") or ""
    rows = frappe.get_all("Vobiz Call Log",
        filters={"name": [">", cursor], "status": ["not in", sorted(TERMINAL)]},
        fields=["name", "status", "response_json", "call_uuid", "a_leg_uuid", "b_leg_uuid"],
        order_by="name asc", limit_page_length=100)
    cache.set_value("vctc:cancel-cursor", rows[-1].name if len(rows) == 100 else "",
                    expires_in_sec=3600)
    for doc in rows:
        queue_pending_cancel(doc)
