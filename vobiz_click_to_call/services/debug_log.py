from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import frappe

MAX_DIAGNOSTIC_PAYLOAD_CHARS = 20 * 1024



@contextmanager
def defer_diagnostics():
    """Buffer routing diagnostics until its locks have been released."""
    previous = getattr(frappe.flags, "vobiz_deferred_diagnostics", None)
    if previous is not None:
        yield
        return
    pending = []
    frappe.flags.vobiz_deferred_diagnostics = pending
    try:
        yield
    finally:
        frappe.flags.vobiz_deferred_diagnostics = previous
        if pending:
            try:
                frappe.enqueue(
                    "vobiz_click_to_call.services.debug_log.write_deferred_diagnostics",
                    queue="default", timeout=120, entries=pending,
                )
            except Exception:
                # Diagnostic failure must not change the call's routing outcome.
                try:
                    frappe.logger("vobiz_incoming").warning("Could not enqueue incoming diagnostics")
                except Exception:
                    pass


def write_deferred_diagnostics(entries):
    for entry in entries:
        log_vobiz_event(**entry)
        frappe.db.commit()

def log_vobiz_event(
    message: str,
    *,
    call_log: str | None = None,
    severity: str = "Info",
    process_type: str = "Webhook",
    payload: Any = None,
    traceback: str | None = None,
) -> None:
    """Write diagnostics synchronously, or buffer them during incoming routing."""
    try:
        if severity == "Info":
            return

        pending = getattr(frappe.flags, "vobiz_deferred_diagnostics", None)
        if pending is not None:
            if len(pending) < 20:
                pending.append(dict(message=message, call_log=call_log, severity=severity,
                                    process_type=process_type, payload=_stringify(payload),
                                    traceback=traceback))
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
        text = payload
    else:
        text = json.dumps(payload, indent=2, default=str)
    if len(text) <= MAX_DIAGNOSTIC_PAYLOAD_CHARS:
        return text
    return (
        text[:MAX_DIAGNOSTIC_PAYLOAD_CHARS]
        + f"\n...[truncated {len(text) - MAX_DIAGNOSTIC_PAYLOAD_CHARS} characters]"
    )
