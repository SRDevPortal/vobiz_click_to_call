from __future__ import annotations

import frappe


TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}


def call_completion_payload(doc):
    """The same complete event contract for document saves and lifecycle updates."""
    fields = (
        "name", "status", "direction", "reference_doctype", "reference_name",
        "start_time", "answer_time", "end_time", "disposition", "disposition_at",
        "call_flow", "call_status",
    )
    payload = {field: doc.get(field) for field in fields}
    payload["customer_number_display"] = doc.get("customer_number")
    return payload


def publish_call_disconnected(doc, method=None) -> None:
    """Notify only the call's agent after a terminal call update commits."""
    if doc.status not in TERMINAL_STATUSES or not doc.user:
        return

    frappe.publish_realtime(
        "vobiz_call_disconnected",
        call_completion_payload(doc),
        user=doc.user,
        after_commit=True,
    )
