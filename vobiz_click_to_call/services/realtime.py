from __future__ import annotations

import frappe


TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}


def publish_call_disconnected(doc, method=None) -> None:
    """Notify only the call's agent after a terminal call update commits."""
    if doc.status not in TERMINAL_STATUSES or not doc.user:
        return

    frappe.publish_realtime(
        "vobiz_call_disconnected",
        {
            "name": doc.name,
            "status": doc.status,
            "reference_doctype": doc.reference_doctype,
            "reference_name": doc.reference_name,
            "customer_number_display": doc.customer_number,
            "start_time": doc.start_time,
            "end_time": doc.end_time,
            "disposition": doc.disposition,
        },
        user=doc.user,
        after_commit=True,
    )
