from __future__ import annotations

import frappe
from frappe import _

from vobiz_ai.api.call_log import sync_linked_summaries
from vobiz_click_to_call.services.call_status import status_bucket
from vobiz_click_to_call.services.debug_log import log_vobiz_event
from vobiz_click_to_call.services.lead_disposition import sync_call_disposition_to_lead
from vobiz_click_to_call.services.safety import block_number
from vobiz_click_to_call.services.settings import get_manual_disposition_options


TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled"}
CONNECTED_STATUSES = {"Connected", "Completed"}
MISSED_STATUSES = {"Failed", "Busy", "No Answer", "Cancelled"}
DND_DISPOSITIONS = {"Wrong Number", "Invalid Number", "Do Not Call"}
AUTO_DIAL_TIMEOUT_STATUS = "Agent Not Available"


def get_patient_followup_status_options() -> list[str]:
    if not frappe.db.exists("DocType", "Patient"):
        return ["Pending"]

    field = frappe.get_meta("Patient").get_field("sr_followup_status")
    if not field:
        return ["Pending"]

    options = [row.strip() for row in str(field.options or "").splitlines() if row.strip()]
    if AUTO_DIAL_TIMEOUT_STATUS not in options:
        options.append(AUTO_DIAL_TIMEOUT_STATUS)
    return options or ["Pending"]


def save_call_disposition(
    *,
    call_log: str,
    disposition: str | None = None,
    notes: str = "",
    lead_status: str | None = None,
    sr_followup_status: str | None = None,
    follow_up_datetime: str | None = None,
    mark_dnd: bool = False,
) -> dict:
    doc = frappe.get_doc("Vobiz Call Log", call_log)
    assert_user_can_update_disposition(doc)

    lead_status = (lead_status or "").strip()
    disposition = (disposition or "").strip()
    sr_followup_status = (sr_followup_status or "").strip()
    notes = (notes or "").strip()
    patient_sync = {"synced": False, "reason": "Reference is not Patient."}
    if doc.reference_doctype == "Patient":
        patient_sync = sync_patient_followup_status(doc.reference_name, sr_followup_status)
        if sr_followup_status and not disposition:
            disposition = sr_followup_status

    allowed_dispositions = get_manual_disposition_options(
        reference_doctype=doc.reference_doctype,
        reference_name=doc.reference_name,
        lead_status=lead_status,
    )
    if doc.reference_doctype != "Patient" and disposition and allowed_dispositions and disposition not in allowed_dispositions:
        frappe.throw(_("Invalid disposition."))

    if disposition:
        sync_call_log_disposition_options(disposition)
        doc.meta = frappe.get_meta("Vobiz Call Log", cached=False)
    doc.disposition = disposition
    doc.disposition_notes = notes
    doc.follow_up_datetime = follow_up_datetime
    doc.disposition_by = frappe.session.user
    doc.disposition_at = frappe.utils.now()

    if disposition and (mark_dnd or disposition in DND_DISPOSITIONS):
        block_number(
            phone_number=doc.customer_number,
            reason="Wrong Number" if disposition in {"Wrong Number", "Invalid Number"} else "Do Not Call",
            reference_doctype=doc.reference_doctype,
            reference_name=doc.reference_name,
            notes=notes,
        )
        doc.dnd_marked = 1
        mark_reference_dnd(doc, disposition, notes)

    if follow_up_datetime:
        doc.follow_up_todo = upsert_follow_up_todo(doc, follow_up_datetime)

    doc.save(ignore_permissions=True)
    if doc.reference_doctype == "Patient":
        lead_sync = patient_sync
    else:
        lead_sync = sync_call_disposition_safely(doc, disposition, lead_status) if (disposition or lead_status) else {"synced": False, "reason": "No CRM status or disposition provided."}
    update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
    sync_linked_summaries(doc)
    add_disposition_comment(doc)
    frappe.db.commit()

    return {
        "call_log": doc.name,
        "disposition": doc.disposition,
        "sr_followup_status": sr_followup_status,
        "follow_up_todo": doc.follow_up_todo,
        "dnd_marked": bool(doc.dnd_marked),
        "lead_sync": lead_sync,
    }


def sync_patient_followup_status(patient: str | None, sr_followup_status: str | None) -> dict:
    sr_followup_status = (sr_followup_status or "").strip()
    if not patient:
        return {"synced": False, "reason": "Patient reference missing."}
    if not frappe.db.exists("DocType", "Patient") or not frappe.db.exists("Patient", patient):
        return {"synced": False, "reason": "Patient not found."}
    meta = frappe.get_meta("Patient")
    field = meta.get_field("sr_followup_status")
    if not field:
        return {"synced": False, "reason": "Patient sr_followup_status field not found."}
    if not sr_followup_status:
        return {"synced": False, "reason": "No follow-up status provided."}

    options = get_patient_followup_status_options()
    if options and sr_followup_status not in options:
        frappe.throw(_("Invalid follow-up status."))

    values = {"sr_followup_status": sr_followup_status}
    status_field = meta.get_field("status")
    status_options = [row.strip() for row in str((status_field and status_field.options) or "").splitlines() if row.strip()]
    if not status_options or "Active" in status_options:
        values["status"] = "Active"

    frappe.db.set_value("Patient", patient, values, update_modified=True)
    return {
        "synced": True,
        "patient": patient,
        "sr_followup_status": sr_followup_status,
        "status": values.get("status"),
    }


def sync_call_log_disposition_options(disposition: str | None = None) -> None:
    try:
        from vobiz_click_to_call.install import ensure_vobiz_call_log_disposition_field

        extra_options = [disposition] if disposition else []
        extra_options.extend(get_patient_followup_status_options())
        ensure_vobiz_call_log_disposition_field(extra_options=extra_options)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Vobiz Call Log disposition selector sync failed")


def assert_user_can_update_disposition(doc) -> None:
    if "System Manager" in frappe.get_roles():
        return
    if doc.user != frappe.session.user:
        frappe.throw(_("Not permitted."))


def sync_call_disposition_safely(doc, disposition: str | None = None, lead_status: str | None = None) -> dict:
    try:
        result = sync_call_disposition_to_lead(doc, disposition, lead_status)
        if not result.get("synced"):
            log_vobiz_event(
                "Vobiz CRM Lead disposition sync skipped",
                call_log=doc.name,
                severity="Warning",
                process_type="AI Processing",
                payload={
                    "reference_doctype": doc.reference_doctype,
                    "reference_name": doc.reference_name,
                    "disposition": disposition,
                    "lead_status": lead_status,
                    "reason": result.get("reason"),
                },
            )
        return result
    except Exception as exc:
        log_vobiz_event(
            "Vobiz CRM Lead disposition sync failed",
            call_log=doc.name,
            severity="Error",
            process_type="AI Processing",
            payload={
                "reference_doctype": doc.reference_doctype,
                "reference_name": doc.reference_name,
                "disposition": disposition,
                "lead_status": lead_status,
                "error": str(exc),
            },
            traceback=frappe.get_traceback(),
        )
        frappe.log_error(frappe.get_traceback(), "Vobiz CRM Lead disposition sync failed")
        return {"synced": False, "reason": str(exc)}


def upsert_follow_up_todo(call_log_doc, follow_up_datetime: str) -> str:
    description = _("Vobiz follow-up for {0}").format(call_log_doc.reference_name or call_log_doc.customer_number)
    if call_log_doc.follow_up_todo and frappe.db.exists("ToDo", call_log_doc.follow_up_todo):
        todo = frappe.get_doc("ToDo", call_log_doc.follow_up_todo)
        todo.date = frappe.utils.getdate(follow_up_datetime)
        todo.description = description
        todo.save(ignore_permissions=True)
        return todo.name

    todo = frappe.get_doc(
        {
            "doctype": "ToDo",
            "allocated_to": call_log_doc.user or frappe.session.user,
            "reference_type": call_log_doc.reference_doctype,
            "reference_name": call_log_doc.reference_name,
            "description": description,
            "date": frappe.utils.getdate(follow_up_datetime),
            "priority": "Medium",
            "status": "Open",
        }
    )
    todo.insert(ignore_permissions=True)
    return todo.name


def update_reference_call_metrics(reference_doctype: str | None, reference_name: str | None) -> None:
    if not reference_doctype or not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        return

    meta = frappe.get_meta(reference_doctype)
    fields = {df.fieldname for df in meta.fields}
    wanted = {
        "vobiz_last_call_status",
        "vobiz_last_call_time",
        "vobiz_last_called_by",
        "vobiz_total_call_attempts",
        "vobiz_connected_call_count",
        "vobiz_missed_call_count",
        "vobiz_last_disposition",
        "vobiz_next_follow_up",
    }
    if not fields.intersection(wanted):
        return

    rows = frappe.get_all(
        "Vobiz Call Log",
        filters={"reference_doctype": reference_doctype, "reference_name": reference_name},
        fields=[
            "name",
            "status",
            "call_status",
            "dial_status",
            "hangup_cause",
            "error_message",
            "call_flow",
            "answer_time",
            "duration",
            "billsec",
            "recording_duration",
            "creation",
            "user",
            "disposition",
            "follow_up_datetime",
        ],
        order_by="creation desc",
    )
    if not rows:
        return

    last = rows[0]
    values = {}
    if "vobiz_last_call_status" in fields:
        values["vobiz_last_call_status"] = call_next_action_label(last)
    if "vobiz_last_call_time" in fields:
        values["vobiz_last_call_time"] = last.creation
    if "vobiz_last_called_by" in fields:
        values["vobiz_last_called_by"] = last.user
    if "vobiz_total_call_attempts" in fields:
        values["vobiz_total_call_attempts"] = len(rows)
    if "vobiz_connected_call_count" in fields:
        values["vobiz_connected_call_count"] = len([row for row in rows if status_bucket(row) == "connected"])
    if "vobiz_missed_call_count" in fields:
        values["vobiz_missed_call_count"] = len(
            [row for row in rows if status_bucket(row) in {"missed", "busy", "no_answer", "failed", "cancelled"}]
        )

    last_disposition = next((row.disposition for row in rows if row.disposition), "")
    if "vobiz_last_disposition" in fields:
        values["vobiz_last_disposition"] = last_disposition

    next_follow_up = next((row.follow_up_datetime for row in rows if row.follow_up_datetime), None)
    if "vobiz_next_follow_up" in fields:
        values["vobiz_next_follow_up"] = next_follow_up

    if values:
        frappe.db.set_value(reference_doctype, reference_name, values, update_modified=False)


def call_next_action_label(call_log_doc) -> str:
    status = str(call_log_doc.get("status") or "")
    if status not in {"Cancelled", "Canceled"}:
        return status

    party = cancelled_call_party(call_log_doc)
    if party == "Agent":
        return "Cancelled by Agent"
    if party == "Customer":
        return "Cancelled by Customer"
    return status


def cancelled_call_party(call_log_doc) -> str:
    signal = " ".join(
        str(call_log_doc.get(fieldname) or "")
        for fieldname in ("status", "call_status", "dial_status", "hangup_cause", "error_message")
    ).strip().lower().replace("_", "-")
    if "cancelled by user" in signal or "canceled by user" in signal:
        return "Agent"
    if "agent" in signal and any(token in signal for token in ("cancel", "reject", "decline", "hangup")):
        return "Agent"
    if "customer" in signal and any(token in signal for token in ("cancel", "reject", "decline", "hangup")):
        return "Customer"

    flow = call_log_doc.get("call_flow") or "Customer First"
    first = "Agent" if flow == "Agent First" else "Customer"
    second = "Customer" if flow == "Agent First" else "Agent"
    answered_first = bool(call_log_doc.get("answer_time")) or call_log_doc.get("status") in {
        "Agent Answered",
        "Customer Answered",
        "Agent Ringing",
        "Connected",
        "Completed",
    }
    return second if answered_first else first


def mark_reference_dnd(call_log_doc, disposition: str, notes: str) -> None:
    if not call_log_doc.reference_doctype or not call_log_doc.reference_name:
        return
    if not frappe.db.exists(call_log_doc.reference_doctype, call_log_doc.reference_name):
        return

    meta = frappe.get_meta(call_log_doc.reference_doctype)
    fields = {df.fieldname for df in meta.fields}
    values = {}
    if "vobiz_do_not_call" in fields:
        values["vobiz_do_not_call"] = 1
    if "vobiz_do_not_call_reason" in fields:
        values["vobiz_do_not_call_reason"] = f"{disposition}: {notes}" if notes else disposition
    if values:
        frappe.db.set_value(call_log_doc.reference_doctype, call_log_doc.reference_name, values, update_modified=True)


def add_disposition_comment(call_log_doc) -> None:
    if not call_log_doc.reference_doctype or not call_log_doc.reference_name:
        return
    if not frappe.db.exists(call_log_doc.reference_doctype, call_log_doc.reference_name):
        return

    try:
        ref = frappe.get_doc(call_log_doc.reference_doctype, call_log_doc.reference_name)
        text = (
            f"Vobiz call disposition: {call_log_doc.disposition or '-'}\n\n"
            f"Status: {call_log_doc.status}\n"
            f"Notes: {call_log_doc.disposition_notes or '-'}"
        )
        if call_log_doc.follow_up_datetime:
            text += f"\nFollow-up: {call_log_doc.follow_up_datetime}"
        ref.add_comment("Comment", text)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Vobiz disposition comment failed")


def get_reference_call_summary(reference_doctype: str, reference_name: str) -> dict:
    if not reference_doctype or not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        frappe.throw(_("Reference document not found."))

    doc = frappe.get_doc(reference_doctype, reference_name)
    if not doc.has_permission("read"):
        frappe.throw(_("Not permitted."))

    rows = frappe.get_all(
        "Vobiz Call Log",
        filters={"reference_doctype": reference_doctype, "reference_name": reference_name},
        fields=[
            "name",
            "creation",
            "status",
            "user",
            "customer_number",
            "duration",
            "disposition",
            "follow_up_datetime",
            "recording_url",
            "ai_disposition",
        ],
        order_by="creation desc",
        limit=10,
    )
    total = frappe.db.count(
        "Vobiz Call Log",
        {"reference_doctype": reference_doctype, "reference_name": reference_name},
    )
    connected = frappe.db.count(
        "Vobiz Call Log",
        {"reference_doctype": reference_doctype, "reference_name": reference_name, "status": ["in", list(CONNECTED_STATUSES)]},
    )
    missed = frappe.db.count(
        "Vobiz Call Log",
        {"reference_doctype": reference_doctype, "reference_name": reference_name, "status": ["in", list(MISSED_STATUSES)]},
    )

    return {
        "total": total,
        "connected": connected,
        "missed": missed,
        "rows": rows,
    }
