from __future__ import annotations

import json
from typing import Any

import frappe


def log_vobiz_event(
    message: str,
    *,
    call_log: str | None = None,
    severity: str = "Info",
    process_type: str = "Webhook",
    payload: Any = None,
    traceback: str | None = None,
) -> None:
    """Write a non-blocking diagnostic row for click-to-call activity."""
    try:
        if severity == "Info":
            return

        if not frappe.db.exists("DocType", "Vobiz Error Log"):
            frappe.log_error(_stringify(payload), f"Vobiz Click To Call: {message}")
            return

        doc = frappe.new_doc("Vobiz Error Log")
        doc.process_type = process_type if process_type in _process_type_options() else "Webhook"
        doc.status = "Open"
        doc.severity = severity if severity in {"Info", "Warning", "Error", "Critical"} else "Info"
        doc.error_message = message[:140] if message else "Vobiz event"
        doc.call_log = call_log if call_log and frappe.db.exists("Vobiz Call Log", call_log) else None
        doc.payload = _stringify(payload)
        doc.traceback = traceback or ""

        if doc.call_log:
            ref = frappe.db.get_value("Vobiz Call Log", doc.call_log, ["crm_lead", "patient"], as_dict=True)
            if ref:
                doc.crm_lead = ref.get("crm_lead")
                doc.patient = ref.get("patient")

        doc.insert(ignore_permissions=True)
    except Exception:
        try:
            frappe.log_error(frappe.get_traceback(), "Vobiz diagnostic logging failed")
        except Exception:
            pass


def _process_type_options() -> set[str]:
    try:
        field = frappe.get_meta("Vobiz Error Log").get_field("process_type")
        return {row.strip() for row in (field.options or "").splitlines() if row.strip()}
    except Exception:
        return {"Webhook"}


def _stringify(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, indent=2, default=str)
