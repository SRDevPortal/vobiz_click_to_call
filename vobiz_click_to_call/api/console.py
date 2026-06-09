from __future__ import annotations

import re
from typing import Any

import frappe
from frappe import _

from vobiz_click_to_call.api.call import get_call_capability, get_call_status, restore_mapping_after_call
from vobiz_click_to_call.api.recording import recording_proxy_url
from vobiz_click_to_call.api.disposition import get_disposition_options_api
from vobiz_click_to_call.services.disposition import CONNECTED_STATUSES, MISSED_STATUSES
from vobiz_click_to_call.services.lead_disposition import get_lead_disposition_context
from vobiz_click_to_call.services.settings import get_settings


TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}
LEAD_DOCTYPE_CANDIDATES = ("CRM Lead", "Lead", "Patient", "Customer")
HTML_TAG_RE = re.compile(r"<[^>]*>")
CONSOLE_SESSION_TTL_SECONDS = 25


def _console_session_key(user: str) -> str:
    return f"vobiz_agent_console:online:{user}"


def is_agent_console_online(user: str | None) -> bool:
    if not user:
        return False
    return bool(frappe.cache().get_value(_console_session_key(user)))


@frappe.whitelist(methods=["POST"])
def heartbeat_agent_console() -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    frappe.cache().set_value(
        _console_session_key(frappe.session.user),
        frappe.utils.now(),
        expires_in_sec=CONSOLE_SESSION_TTL_SECONDS,
    )
    return {
        "online": True,
        "ttl": CONSOLE_SESSION_TTL_SECONDS,
    }


@frappe.whitelist()
def get_agent_console_data(limit: int | str = 25, search: str | None = None) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    limit = max(5, min(frappe.utils.cint(limit) or 25, 500))
    settings = get_settings()
    return {
        "summary": _call_summary(),
        "availability": get_call_capability(),
        "active_call": _active_call(),
        "queue": _lead_queue(limit, search),
        "dispositions": get_disposition_options_api(),
        "ai_disposition_enabled": bool(settings.enable_ai_disposition),
    }


@frappe.whitelist()
def get_reference_context(reference_doctype: str, reference_name: str, lite: int | str = 0) -> dict[str, Any]:
    doc = _get_permitted_reference(reference_doctype, reference_name)
    lite = bool(frappe.utils.cint(lite))
    history = _call_history(reference_doctype, reference_name, 8)

    return {
        "reference": _reference_row(reference_doctype, doc),
        "history": history,
        "guidance": _guidance_for_reference(reference_doctype, doc),
        "workdesk": _workdesk_context(reference_doctype, reference_name, doc, lite=lite, history=history),
    }


@frappe.whitelist()
def get_workdesk_tab(reference_doctype: str, reference_name: str, tab: str) -> dict[str, Any]:
    doc = _get_permitted_reference(reference_doctype, reference_name)
    tab = (tab or "").strip()
    lead_name = reference_name if reference_doctype == "CRM Lead" else None
    patient = _resolve_patient(reference_doctype, reference_name, doc)

    if tab == "encounters":
        return {
            "encounters": _related_encounters(lead_name, patient),
            "appointments": _related_appointments(patient),
            "sales_invoices": _related_sales_invoices(patient, doc),
        }
    if tab == "clinical-history":
        return {"clinical_history": _patient_clinical_history(patient)}
    if tab == "reports":
        return {"reports": _related_reports(lead_name, patient, reference_doctype, reference_name)}
    if tab == "vobiz":
        history = _call_history(reference_doctype, reference_name, 8)
        return {"history": history, "vobiz": _vobiz_summary_from_history(history)}
    if tab == "whatsapp":
        return {"whatsapp": _whatsapp_preview(reference_doctype, reference_name)}

    return _workdesk_context(reference_doctype, reference_name, doc, lite=True)


def _get_permitted_reference(reference_doctype: str, reference_name: str):
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if not reference_doctype or not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        frappe.throw(_("Reference not found."))

    doc = frappe.get_doc(reference_doctype, reference_name)
    if not doc.has_permission("read"):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    return doc


@frappe.whitelist()
def get_whatsapp_conversation(reference_doctype: str, reference_name: str) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if not reference_doctype or not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        frappe.throw(_("Reference not found."))

    conversation = _conversation_for_reference_phone(reference_doctype, reference_name)
    if conversation:
        return {"success": True, "conversation": conversation}

    phone = _reference_phone_for_whatsapp(reference_doctype, reference_name)
    if phone:
        return {
            "success": False,
            "conversation": None,
            "message": _("No WhatsApp chat found for mobile number {0}.").format(_last_10_digits(phone)),
        }
    return {
        "success": False,
        "conversation": None,
        "message": _("Add a mobile number on this record to open WhatsApp chat."),
    }


@frappe.whitelist()
def get_whatsapp_messages(conversation: str, limit: int | str = 10, before: str | None = None) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    _ensure_whatsapp_conversation_read(conversation)
    page = _whatsapp_messages_page(conversation, limit, before)
    return {"success": True, **page}


@frappe.whitelist(methods=["POST"])
def send_whatsapp_reply(conversation: str, body: str) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if not (body or "").strip():
        frappe.throw(_("Message is required."))

    _ensure_whatsapp_conversation_read(conversation)

    try:
        from wa_chat_hub.outbound import send_outbound_message
        from wa_chat_hub.services import append_message

        outbound = send_outbound_message(conversation, body.strip(), "Text")
        delivery_status = outbound.get("delivery_status") or "Sent"
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "Vobiz Workdesk WhatsApp Send Failed")
        outbound = {
            "conversation": conversation,
            "body": body.strip(),
            "content_type": "Text",
            "sent": False,
            "delivery_status": "Failed",
            "error": str(exc),
        }
        delivery_status = "Failed"

    convo = frappe.get_doc("Chat Conversation", conversation)
    contact_phone = frappe.db.get_value("Chat Contact", convo.contact, "phone_number")
    result = append_message({
        "channel_account": convo.channel_account,
        "phone_number": contact_phone,
        "direction": "Outbound",
        "sender_type": "Agent",
        "content_type": "Text",
        "body": body.strip(),
        "delivery_status": delivery_status,
        "channel_message_id": outbound.get("provider_message_id"),
        "raw_transport_payload": outbound,
    })
    frappe.db.commit()
    return {"success": True, "result": {**outbound, **result}, **_whatsapp_messages_page(conversation, 10)}


@frappe.whitelist()
def get_active_call() -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    return _active_call() or {}


@frappe.whitelist()
def get_call_performance(status_filter: str | None = None) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    start = f"{frappe.utils.today()} 00:00:00"
    end = f"{frappe.utils.today()} 23:59:59"
    filters: dict[str, Any] = {"creation": ["between", [start, end]]}
    is_admin = "System Manager" in frappe.get_roles()
    if not is_admin:
        filters["user"] = frappe.session.user

    rows = frappe.get_all(
        "Vobiz Call Log",
        filters=filters,
        fields=[
            "name",
            "user",
            "status",
            "duration",
            "billsec",
            "creation",
            "reference_doctype",
            "reference_name",
            "customer_number",
            "user_mobile",
            "disposition",
        ],
        order_by="creation desc",
        limit_page_length=1000,
    )
    filtered_rows = _filter_performance_rows(rows, status_filter)
    return {
        "is_admin": is_admin,
        "status_filter": status_filter or "total",
        "summary": _performance_summary(filtered_rows),
        "overall": _performance_summary(rows),
        "agents": _performance_by_user(filtered_rows) if is_admin else [],
        "calls": [_performance_call_row(row) for row in filtered_rows[:100]],
    }


def _call_summary() -> dict[str, Any]:
    start = f"{frappe.utils.today()} 00:00:00"
    end = f"{frappe.utils.today()} 23:59:59"
    filters: dict[str, Any] = {"creation": ["between", [start, end]]}
    if "System Manager" not in frappe.get_roles():
        filters["user"] = frappe.session.user

    rows = frappe.get_all(
        "Vobiz Call Log",
        filters=filters,
        fields=["status", "duration", "billsec"],
        limit_page_length=1000,
    )
    connected = [row for row in rows if row.status in CONNECTED_STATUSES or row.status == "Connected"]
    missed = [row for row in rows if row.status in MISSED_STATUSES or row.status in {"Cancelled", "Canceled"}]
    durations = [frappe.utils.cint(row.billsec or row.duration) for row in rows if frappe.utils.cint(row.billsec or row.duration)]
    average_duration = round(sum(durations) / len(durations)) if durations else 0

    return {
        "connected": len(connected),
        "missed": len(missed),
        "total": len(rows),
        "average_duration": average_duration,
        "average_duration_label": _duration_label(average_duration),
    }


def _performance_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    connected = [row for row in rows if row.status in CONNECTED_STATUSES or row.status == "Connected"]
    missed = [row for row in rows if row.status in MISSED_STATUSES or row.status in {"Cancelled", "Canceled"}]
    durations = [frappe.utils.cint(row.billsec or row.duration) for row in rows if frappe.utils.cint(row.billsec or row.duration)]
    average_duration = round(sum(durations) / len(durations)) if durations else 0
    return {
        "total": len(rows),
        "connected": len(connected),
        "missed": len(missed),
        "average_duration": average_duration,
        "average_duration_label": _duration_label(average_duration),
    }


def _filter_performance_rows(rows: list[dict[str, Any]], status_filter: str | None) -> list[dict[str, Any]]:
    status_filter = (status_filter or "total").strip().lower()
    if status_filter == "connected":
        return [row for row in rows if row.status in CONNECTED_STATUSES or row.status == "Connected"]
    if status_filter == "missed":
        return [row for row in rows if row.status in MISSED_STATUSES or row.status in {"Cancelled", "Canceled"}]
    return rows


def _performance_by_user(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.user or _("Unassigned"), []).append(row)
    data = []
    for user, user_rows in grouped.items():
        data.append({"user": user, **_performance_summary(user_rows)})
    return sorted(data, key=lambda row: row["total"], reverse=True)


def _performance_call_row(row) -> dict[str, Any]:
    seconds = frappe.utils.cint(row.billsec or row.duration)
    return {
        "name": row.name,
        "user": row.user,
        "status": row.status,
        "duration_label": _duration_label(seconds),
        "creation": row.creation,
        "reference_doctype": row.reference_doctype,
        "reference_name": row.reference_name,
        "customer_number": row.customer_number,
        "user_mobile": row.user_mobile,
        "disposition": row.disposition,
    }


def _active_call() -> dict[str, Any] | None:
    mapping_name = frappe.db.get_value("Vobiz User Mapping", {"user": frappe.session.user, "enabled": 1}, "name")
    if not mapping_name:
        return None

    mapping = frappe.get_doc("Vobiz User Mapping", mapping_name)
    call_log = mapping.get("current_call_log")
    if not call_log or not frappe.db.exists("Vobiz Call Log", call_log):
        return {
            "mapping": mapping_name,
            "availability_status": mapping.get("availability_status") or "Available",
        }

    status = get_call_status(call_log)
    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if doc.status in TERMINAL_STATUSES:
        restore_mapping_after_call(doc.name)
        frappe.db.commit()
        return {
            "mapping": mapping_name,
            "availability_status": mapping.get("availability_status") or "Available",
            "last_call": status,
        }
    return {
        **status,
        "mapping": mapping_name,
        "availability_status": mapping.get("availability_status") or "Busy",
        "reference_doctype": doc.reference_doctype,
        "reference_name": doc.reference_name,
        "started_at": doc.start_time or doc.creation,
    }


def _agent_context() -> dict[str, Any]:
    if not frappe.db.exists("DocType", "Vobiz User Mapping"):
        return {}

    return frappe.db.get_value(
        "Vobiz User Mapping",
        {"user": frappe.session.user, "enabled": 1},
        ["name", "agent_mobile", "caller_id", "availability_status"],
        as_dict=True,
    ) or {}


def _lead_queue(limit: int, search: str | None = None) -> list[dict[str, Any]]:
    for doctype in LEAD_DOCTYPE_CANDIDATES:
        if frappe.db.exists("DocType", doctype):
            rows = _queue_for_doctype(doctype, limit, search)
            if doctype == "CRM Lead":
                return rows
            if rows:
                return rows
    return []


def _queue_for_doctype(doctype: str, limit: int, search: str | None = None) -> list[dict[str, Any]]:
    meta = frappe.get_meta(doctype)
    fields = ["name", "modified"]
    for fieldname in _existing_fields(
        meta,
        (
            "lead_name",
            "first_name",
            "patient_name",
            "customer_name",
            "company_name",
            "organization",
            "mobile_no",
            "mobile",
            "phone",
            "phone_no",
            "status",
            "lead_status",
            "sr_lead_status",
            "qualification_status",
            "vobiz_last_call_status",
            "vobiz_next_follow_up",
            "lead_owner",
            "owner",
        ),
    ):
        if fieldname not in fields:
            fields.append(fieldname)

    filters = _queue_filters(meta)
    query = (search or "").strip()
    search_fields = _queue_search_fields(meta, fields) if query else []
    rows = frappe.get_all(
        doctype,
        filters=filters,
        or_filters=_queue_search_filters(search_fields, query) if query else None,
        fields=fields,
        order_by="modified desc",
        limit_page_length=limit,
    )
    return [_reference_row(doctype, row) for row in rows]


def _queue_filters(meta) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if meta.has_field("disabled"):
        filters["disabled"] = ["!=", 1]
    if meta.has_field("vobiz_do_not_call"):
        filters["vobiz_do_not_call"] = ["!=", 1]
    if meta.name == "CRM Lead" and meta.has_field("lead_owner") and not _can_view_all_queue_leads():
        filters["lead_owner"] = frappe.session.user
    return filters


def _queue_search_fields(meta, loaded_fields: list[str]) -> list[str]:
    candidates = (
        "name",
        "lead_name",
        "first_name",
        "patient_name",
        "customer_name",
        "company_name",
        "organization",
        "mobile_no",
        "mobile",
        "phone",
        "phone_no",
        "status",
        "lead_status",
        "sr_lead_status",
        "qualification_status",
    )
    return [fieldname for fieldname in candidates if fieldname == "name" or fieldname in loaded_fields or meta.has_field(fieldname)]


def _queue_search_filters(fields: list[str], query: str) -> list[list[str]]:
    if not fields or not query:
        return []
    return [[fieldname, "like", f"%{query}%"] for fieldname in fields]


def _can_view_all_queue_leads() -> bool:
    return frappe.session.user == "Administrator" or "System Manager" in frappe.get_roles()


def _reference_row(doctype: str, doc) -> dict[str, Any]:
    data = doc.as_dict() if callable(getattr(doc, "as_dict", None)) else dict(doc)
    title = (
        data.get("lead_name")
        or data.get("patient_name")
        or data.get("customer_name")
        or data.get("first_name")
        or data.get("company_name")
        or data.get("name")
    )
    company = data.get("company_name") or data.get("organization") or doctype
    phone_field = _first_value(data, ("mobile_no", "mobile", "phone", "phone_no"))
    status = _first_value(data, ("status", "lead_status", "sr_lead_status", "qualification_status")).get("value") or "New"
    next_action = data.get("vobiz_next_follow_up") or data.get("vobiz_last_call_status") or "Initial contact"

    return {
        "doctype": doctype,
        "name": data.get("name"),
        "title": title,
        "company": company,
        "phone": phone_field.get("value"),
        "phone_field": phone_field.get("fieldname"),
        "status": status,
        "next_action": str(next_action),
        "owner": data.get("lead_owner") or data.get("owner"),
    }


def _call_history(reference_doctype: str, reference_name: str, limit: int) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        "Vobiz Call Log",
        filters={"reference_doctype": reference_doctype, "reference_name": reference_name},
        fields=[
            "name",
            "status",
            "disposition",
            "duration",
            "billsec",
            "creation",
            "customer_number",
            "user",
            "user_mobile",
            "ai_summary",
            "ai_next_action",
            "recording_status",
            "recording_url",
            "recording_id",
            "recording_duration",
            "transcript_status",
            "transcript_text",
            "transcript_error",
            "hangup_cause",
        ],
        order_by="creation desc",
        limit_page_length=limit,
    )
    for row in rows:
        row["duration_label"] = _duration_label(frappe.utils.cint(row.billsec or row.duration))
        row["recording_download_url"] = recording_proxy_url(row.name) if row.recording_url else ""
    return rows


def _guidance_for_reference(reference_doctype: str, doc) -> dict[str, Any]:
    status = doc.get("status") or doc.get("lead_status") or ""
    name = doc.get("lead_name") or doc.get("patient_name") or doc.get("customer_name") or doc.name
    script = [
        _("Confirm you are speaking with {0}.").format(name),
        _("Ask for the purpose of the call and verify the next best action."),
        _("Record disposition immediately after the call."),
    ]
    if status:
        script.insert(1, _("Current status is {0}; continue from that stage.").format(status))

    return {
        "script": script,
        "next_actions": [
            _("Schedule follow-up"),
            _("Send proposal/details"),
            _("Mark not interested or do-not-call if requested"),
        ],
    }


def _workdesk_context(
    reference_doctype: str,
    reference_name: str,
    doc,
    lite: bool = False,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    lead_name = reference_name if reference_doctype == "CRM Lead" else None
    patient = _resolve_patient(reference_doctype, reference_name, doc, allow_phone_lookup=not lite)
    context = {
        "agent": _agent_context(),
        "lead": _lead_details(reference_doctype, reference_name, doc, include_conversation_ai=not lite),
        "lead_disposition": get_lead_disposition_context(reference_doctype, reference_name),
        "patient": patient,
        "vobiz": _vobiz_summary_from_history(history) if history is not None else _vobiz_summary(reference_doctype, reference_name),
        "whatsapp": _whatsapp_deferred(reference_doctype, reference_name),
        "create_defaults": _create_defaults(reference_doctype, reference_name, doc, patient),
        "deferred_tabs": ["encounters", "clinical-history", "reports", "whatsapp"],
    }

    if lite:
        context.update({
            "encounters": [],
            "clinical_history": {"patient": None, "rows": []},
            "appointments": [],
            "sales_invoices": [],
            "reports": {"files": [], "ocr": [], "insights": []},
        })
        return context

    return {
        **context,
        "encounters": _related_encounters(lead_name, patient),
        "clinical_history": _patient_clinical_history(patient),
        "appointments": _related_appointments(patient),
        "sales_invoices": _related_sales_invoices(patient, doc),
        "reports": _related_reports(lead_name, patient, reference_doctype, reference_name),
        "whatsapp": _whatsapp_preview(reference_doctype, reference_name),
        "deferred_tabs": [],
    }


def _lead_details(
    reference_doctype: str,
    reference_name: str,
    doc,
    include_conversation_ai: bool = True,
) -> dict[str, Any]:
    if reference_doctype != "CRM Lead":
        return {"doctype": reference_doctype, "name": reference_name, "fields": []}

    meta = frappe.get_meta("CRM Lead")
    wa_ai = _conversation_ai_fields(reference_doctype, reference_name) if include_conversation_ai else {}
    wanted = (
        "status",
        "sr_lead_platform",
        "sr_lead_disposition",
        "source",
        "sr_lead_pipeline",
        "lead_score",
        "lead_lan",
        "lead_temperature",
        "mobile_no",
        "phone",
        "email",
        "sr_source_patient",
    )
    fields = []
    for fieldname in wanted:
        df = meta.get_field(fieldname)
        if not df:
            continue
        value = doc.get(fieldname)
        if fieldname == "lead_score" and _is_empty_score(value):
            value = wa_ai.get("lead_score")
        elif fieldname in {"lead_lan", "lead_temperature"} and not value:
            value = wa_ai.get(fieldname)
        fields.append(
            {
                "fieldname": fieldname,
                "label": df.label or fieldname,
                "value": value,
                "fieldtype": df.fieldtype,
            }
        )
    return {"doctype": reference_doctype, "name": reference_name, "fields": fields}


def _conversation_ai_fields(reference_doctype: str, reference_name: str) -> dict[str, Any]:
    conversation = _conversation_for_reference_phone(reference_doctype, reference_name)
    if not conversation or not frappe.db.exists("DocType", "Chat Conversation"):
        return {}

    meta = frappe.get_meta("Chat Conversation")
    fields = _existing_fields(meta, ("lead_score", "lead_lan", "lead_temperature"))
    if not fields:
        return {}
    return frappe.db.get_value("Chat Conversation", conversation, fields, as_dict=True) or {}


def _is_empty_score(value) -> bool:
    if value in (None, ""):
        return True
    try:
        return float(value) == 0
    except Exception:
        return False


def _resolve_patient(
    reference_doctype: str,
    reference_name: str,
    doc,
    allow_phone_lookup: bool = True,
) -> str | None:
    meta = frappe.get_meta(reference_doctype)
    for fieldname in ("sr_source_patient", "patient"):
        if meta.has_field(fieldname) and doc.get(fieldname):
            return doc.get(fieldname)

    if not allow_phone_lookup:
        return None

    phone = _first_value(doc.as_dict(), ("mobile_no", "mobile", "phone", "phone_no")).get("value")
    if not phone or not frappe.db.exists("DocType", "Patient"):
        return None

    candidates = ("mobile", "mobile_no", "phone", "custom_whatsapp_number")
    patient_meta = frappe.get_meta("Patient")
    last10 = str(phone)[-10:]
    for fieldname in candidates:
        if patient_meta.has_field(fieldname):
            patient = frappe.db.get_value("Patient", {fieldname: ["like", f"%{last10}%"]}, "name")
            if patient:
                return patient
    return None


def _related_encounters(lead_name: str | None, patient: str | None) -> list[dict[str, Any]]:
    if not frappe.db.exists("DocType", "Patient Encounter"):
        return []
    meta = frappe.get_meta("Patient Encounter")
    filters_list = []
    if lead_name and meta.has_field("sr_source_crm_lead"):
        filters_list.append({"sr_source_crm_lead": lead_name})
    if patient and meta.has_field("patient"):
        filters_list.append({"patient": patient})
    return _dedup_rows("Patient Encounter", filters_list, _existing_fields(meta, (
        "name", "patient", "patient_name", "encounter_date", "encounter_time", "sr_encounter_type",
        "sr_encounter_status", "sr_source_crm_lead", "invoiced", "modified",
    )), "modified desc", 8)


def _patient_clinical_history(patient: str | None) -> dict[str, Any]:
    if not patient or not frappe.db.exists("DocType", "Patient") or not frappe.db.exists("DocType", "Patient Encounter"):
        return {"patient": None, "rows": []}

    patient_meta = frappe.get_meta("Patient")
    patient_fields = _existing_fields(patient_meta, (
        "name", "patient_name", "first_name", "sr_patient_id", "patient_id", "sex", "gender",
        "mobile", "mobile_no", "sr_mobile_no", "phone", "phone_no", "sr_phone_no",
    ))
    patient_row = frappe.db.get_value("Patient", patient, patient_fields, as_dict=True) if patient_fields else {"name": patient}

    encounter_meta = frappe.get_meta("Patient Encounter")
    if not encounter_meta.has_field("patient"):
        return {"patient": patient_row, "rows": []}

    base_fields = _existing_fields(encounter_meta, (
        "name", "patient", "patient_name", "encounter_date", "encounter_time", "practitioner",
        "practitioner_name", "sr_complaints", "sr_observations", "sr_investigations",
        "sr_diagnosis", "sr_notes", "modified", "creation",
    ))
    rows = frappe.get_all(
        "Patient Encounter",
        filters={"patient": patient},
        fields=base_fields,
        order_by="encounter_date desc, creation desc",
        limit_page_length=50,
    )

    history = []
    for row in rows:
        data = dict(row)
        has_notes = any(_plain_text(data.get(fieldname)) for fieldname in (
            "sr_complaints", "sr_observations", "sr_investigations", "sr_diagnosis", "sr_notes",
        ))
        full_doc = None
        medications = {}
        for table_field in ("drug_prescription", "sr_homeopathy_drug_prescription", "sr_allopathy_drug_prescription"):
            if encounter_meta.has_field(table_field):
                full_doc = full_doc or frappe.get_doc("Patient Encounter", row.name)
                child_rows = []
                for child in full_doc.get(table_field) or []:
                    child_rows.append({
                        "medication": child.get("medication") or child.get("drug"),
                        "dosage": child.get("dosage"),
                        "period": child.get("period"),
                        "dosage_form": child.get("dosage_form"),
                        "sr_drug_instruction": child.get("sr_drug_instruction"),
                    })
                medications[table_field] = child_rows
                has_notes = has_notes or bool(child_rows)
        if has_notes:
            data["medications"] = medications
            history.append(data)
        if len(history) >= 12:
            break

    return {"patient": patient_row, "rows": history}


def _related_appointments(patient: str | None) -> list[dict[str, Any]]:
    if not patient or not frappe.db.exists("DocType", "Patient Appointment"):
        return []
    meta = frappe.get_meta("Patient Appointment")
    if not meta.has_field("patient"):
        return []
    return frappe.get_all(
        "Patient Appointment",
        filters={"patient": patient},
        fields=_existing_fields(meta, (
            "name", "patient", "patient_name", "appointment_date", "appointment_time",
            "appointment_datetime", "status", "practitioner", "department", "modified",
        )),
        order_by="modified desc",
        limit_page_length=8,
    )


def _related_sales_invoices(patient: str | None, doc) -> list[dict[str, Any]]:
    if not frappe.db.exists("DocType", "Sales Invoice"):
        return []
    meta = frappe.get_meta("Sales Invoice")
    filters_list = []
    if patient and meta.has_field("patient"):
        filters_list.append({"patient": patient})
    if patient and meta.has_field("sr_si_patient_id"):
        patient_id = None
        if frappe.db.exists("DocType", "Patient") and frappe.get_meta("Patient").has_field("sr_patient_id"):
            patient_id = frappe.db.get_value("Patient", patient, "sr_patient_id")
        filters_list.append({"sr_si_patient_id": patient_id or patient})
    customer = doc.get("customer") if callable(getattr(doc, "get", None)) else None
    if customer and meta.has_field("customer"):
        filters_list.append({"customer": customer})
    return _dedup_rows("Sales Invoice", filters_list, _existing_fields(meta, (
        "name", "customer", "posting_date", "grand_total", "outstanding_amount", "status",
        "patient", "sr_si_patient_id", "sr_si_patient_department", "modified",
    )), "modified desc", 8)


def _related_reports(
    lead_name: str | None,
    patient: str | None,
    reference_doctype: str,
    reference_name: str,
) -> dict[str, list[dict[str, Any]]]:
    reports: dict[str, list[dict[str, Any]]] = {"files": [], "ocr": [], "insights": []}
    reports["files"] = _related_files(reference_doctype, reference_name, patient)

    if lead_name and frappe.db.exists("DocType", "WA Lead OCR Result"):
        meta = frappe.get_meta("WA Lead OCR Result")
        reports["ocr"] = frappe.get_all(
            "WA Lead OCR Result",
            filters={"lead": lead_name},
            fields=_existing_fields(meta, ("name", "file", "conversation", "pipeline", "confidence", "status", "raw_text", "modified")),
            order_by="modified desc",
            limit_page_length=6,
        )
    if lead_name and frappe.db.exists("DocType", "WA Lead AI Insight"):
        meta = frappe.get_meta("WA Lead AI Insight")
        reports["insights"] = frappe.get_all(
            "WA Lead AI Insight",
            filters={"lead": lead_name},
            fields=_existing_fields(meta, ("name", "conversation", "pipeline", "insight_type", "confidence", "output_json", "applied_fields", "modified")),
            order_by="modified desc",
            limit_page_length=6,
        )
    return reports


def _related_files(reference_doctype: str, reference_name: str, patient: str | None) -> list[dict[str, Any]]:
    if not frappe.db.exists("DocType", "File"):
        return []
    filters_list = [{"attached_to_doctype": reference_doctype, "attached_to_name": reference_name}]
    if patient:
        filters_list.append({"attached_to_doctype": "Patient", "attached_to_name": patient})
    return _dedup_rows("File", filters_list, ["name", "file_name", "file_url", "file_type", "file_size", "attached_to_doctype", "attached_to_name", "modified"], "modified desc", 10)


def _vobiz_summary(reference_doctype: str, reference_name: str) -> dict[str, Any]:
    history = _call_history(reference_doctype, reference_name, 8)
    return _vobiz_summary_from_history(history)


def _vobiz_summary_from_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    latest = history[0] if history else {}
    connected = len([row for row in history if row.get("status") in CONNECTED_STATUSES or row.get("status") == "Connected"])
    missed = len([row for row in history if row.get("status") in MISSED_STATUSES or row.get("status") in {"Cancelled", "Canceled"}])
    return {
        "latest": latest,
        "history": history,
        "total": len(history),
        "connected": connected,
        "missed": missed,
    }


def _whatsapp_deferred(reference_doctype: str, reference_name: str) -> dict[str, Any]:
    if not frappe.db.exists("DocType", "Chat Conversation"):
        return {"available": False, "message": _("WA Chat Hub is not installed.")}
    return {
        "available": True,
        "conversation": None,
        "messages": [],
        "has_more": False,
        "next_before": None,
        "deferred": True,
        "message": _("WhatsApp will load when the tab opens."),
    }


def _whatsapp_preview(reference_doctype: str, reference_name: str) -> dict[str, Any]:
    if not frappe.db.exists("DocType", "Chat Conversation"):
        return {"available": False, "message": _("WA Chat Hub is not installed.")}

    conversation = _conversation_for_reference_phone(reference_doctype, reference_name)

    if not conversation:
        return {"available": True, "conversation": None}

    fields = _existing_fields(frappe.get_meta("Chat Conversation"), (
        "name", "status", "priority", "lead_score", "lead_lan", "lead_temperature",
        "last_message_preview", "unread_count", "ai_summary", "modified",
    ))
    data = frappe.db.get_value("Chat Conversation", conversation, fields, as_dict=True) if fields else {"name": conversation}
    page = _whatsapp_messages_page(conversation, 10)
    return {
        "available": True,
        "conversation": conversation,
        "data": data,
        **page,
    }


def _whatsapp_recent_messages(conversation: str) -> list[dict[str, Any]]:
    return _whatsapp_messages_page(conversation, 10).get("messages", [])


def _whatsapp_messages_page(conversation: str, limit: int | str = 10, before: str | None = None) -> dict[str, Any]:
    if not conversation or not frappe.db.exists("DocType", "Chat Message"):
        return {"messages": [], "has_more": False, "next_before": None}

    limit = max(1, min(frappe.utils.cint(limit) or 10, 50))
    meta = frappe.get_meta("Chat Message")
    fields = _existing_fields(meta, (
        "name", "direction", "sender_type", "content_type", "body", "media_url", "delivery_status", "creation",
    ))
    filters: dict[str, Any] = {"conversation": conversation}
    if before:
        filters["creation"] = ["<", before]
    rows = frappe.get_all(
        "Chat Message",
        filters=filters,
        fields=fields,
        order_by="creation desc",
        limit_page_length=limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return {
        "messages": rows,
        "has_more": has_more,
        "next_before": rows[0].get("creation") if rows else before,
    }


def _ensure_whatsapp_conversation_read(conversation: str) -> None:
    if not conversation or not frappe.db.exists("Chat Conversation", conversation):
        frappe.throw(_("WhatsApp conversation not found."))
    try:
        from wa_chat_hub.permissions import ensure_can_read_conversation

        ensure_can_read_conversation(conversation)
    except ImportError:
        if not frappe.get_doc("Chat Conversation", conversation).has_permission("read"):
            frappe.throw(_("Not permitted."), frappe.PermissionError)


def _create_defaults(reference_doctype: str, reference_name: str, doc, patient: str | None) -> dict[str, Any]:
    phone = _first_value(doc.as_dict(), ("mobile_no", "mobile", "phone", "phone_no")).get("value")
    customer = doc.get("customer") if callable(getattr(doc, "get", None)) else None
    patient_id = None
    if not customer and patient and frappe.db.exists("DocType", "Patient"):
        patient_fields = ["customer"]
        if frappe.get_meta("Patient").has_field("sr_patient_id"):
            patient_fields.append("sr_patient_id")
        patient_row = frappe.db.get_value("Patient", patient, patient_fields, as_dict=True) or {}
        customer = patient_row.get("customer")
        patient_id = patient_row.get("sr_patient_id")

    return {
        "Patient Encounter": {
            "patient": patient,
            "sr_source_crm_lead": reference_name if reference_doctype == "CRM Lead" else None,
            "sr_encounter_source": doc.get("source") if callable(getattr(doc, "get", None)) else None,
            "sr_lead_notes": doc.get("sr_lead_notes") or doc.get("sr_lead_message") if callable(getattr(doc, "get", None)) else None,
            "sr_encounter_type": "Followup",
            "sr_encounter_place": "Online",
        },
        "Patient Appointment": {
            "patient": patient,
            "apt_mobile_number": phone,
        },
        "Sales Invoice": {
            "patient": patient,
            "customer": customer,
            "sr_si_patient_id": patient_id,
            "sr_si_order_source": doc.get("source") if callable(getattr(doc, "get", None)) else None,
            "sr_si_encounter_place": "Online",
        },
    }


def _conversation_for_reference_phone(reference_doctype: str, reference_name: str) -> str | None:
    phone = _reference_phone_for_whatsapp(reference_doctype, reference_name)
    last10 = _last_10_digits(phone)
    if not last10 or not frappe.db.exists("DocType", "Chat Contact") or not frappe.db.exists("DocType", "Chat Conversation"):
        return None

    contacts = frappe.get_all(
        "Chat Contact",
        filters={"phone_number": ["like", f"%{last10}"]},
        fields=["name", "phone_number", "modified"],
        order_by="modified desc",
        limit_page_length=20,
    )
    for contact in contacts:
        if _last_10_digits(contact.get("phone_number")) != last10:
            continue
        conversation = frappe.db.get_value(
            "Chat Conversation",
            {"contact": contact.name},
            "name",
            order_by="modified desc",
        )
        if conversation:
            return conversation
    return None


def _reference_phone_for_whatsapp(reference_doctype: str, reference_name: str) -> str | None:
    doc = frappe.get_doc(reference_doctype, reference_name)
    meta = frappe.get_meta(reference_doctype)
    for fieldname in ("mobile_no", "mobile", "phone", "phone_no", "whatsapp_number", "whatsapp_no", "custom_whatsapp_number"):
        if meta.has_field(fieldname) and doc.get(fieldname):
            return doc.get(fieldname)

    patient = None
    for fieldname in ("sr_source_patient", "patient"):
        if meta.has_field(fieldname) and doc.get(fieldname):
            patient = doc.get(fieldname)
            break
    if patient and frappe.db.exists("DocType", "Patient") and frappe.db.exists("Patient", patient):
        patient_doc = frappe.get_doc("Patient", patient)
        patient_meta = frappe.get_meta("Patient")
        for fieldname in ("mobile", "mobile_no", "phone", "custom_whatsapp_number"):
            if patient_meta.has_field(fieldname) and patient_doc.get(fieldname):
                return patient_doc.get(fieldname)
    return None


def _last_10_digits(phone: str | None) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def _dedup_rows(
    doctype: str,
    filters_list: list[dict[str, Any]],
    fields: list[str],
    order_by: str,
    limit: int,
) -> list[dict[str, Any]]:
    seen = set()
    rows = []
    for filters in filters_list:
        if not filters:
            continue
        for row in frappe.get_all(doctype, filters=filters, fields=fields, order_by=order_by, limit_page_length=limit):
            if row.name in seen:
                continue
            seen.add(row.name)
            rows.append(row)
    rows.sort(key=lambda row: str(row.get("modified") or ""), reverse=True)
    return rows[:limit]


def _existing_fields(meta, candidates: tuple[str, ...]) -> list[str]:
    standard_fields = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
    return [fieldname for fieldname in candidates if fieldname in standard_fields or meta.has_field(fieldname)]


def _plain_text(value) -> str:
    if value in (None, ""):
        return ""
    return HTML_TAG_RE.sub("", str(value)).replace("&nbsp;", " ").strip()


def _first_value(data: dict[str, Any], candidates: tuple[str, ...]) -> dict[str, Any]:
    for fieldname in candidates:
        value = data.get(fieldname)
        if value:
            return {"fieldname": fieldname, "value": value}
    return {"fieldname": None, "value": None}


def _duration_label(seconds: int) -> str:
    seconds = frappe.utils.cint(seconds)
    minutes, remainder = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {remainder:02d}s"
    return f"{remainder}s"
