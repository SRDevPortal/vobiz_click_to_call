from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from vobiz_click_to_call.api.call import (
    _record_availability_attendance,
    _should_apply_idle_auto_offline,
    get_call_capability,
    get_call_status,
    is_active_call_log,
    restore_mapping_after_call,
)
from vobiz_click_to_call.api.recording import recording_proxy_url
from vobiz_click_to_call.api.disposition import get_disposition_options_api, get_patient_followup_status_options_api
from vobiz_click_to_call.services.attendance import agent_attendance_enabled
from vobiz_click_to_call.services.call_status import MISSED_STATUSES, is_inbound_missed_call, status_bucket, talk_seconds
from vobiz_click_to_call.services.lead_disposition import get_lead_disposition_context
from vobiz_click_to_call.services.settings import get_idle_auto_offline_config, get_settings


TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}
LEAD_DOCTYPE_CANDIDATES = ("CRM Lead", "Lead", "Patient", "Customer")
QUEUE_SOURCE_DOCTYPES = {
    "CRM Lead": "CRM Lead",
    "Patient": "Patient",
    "CRM Lead and Patient": "",
    "Patient Encounter": "Patient Encounter",
    "Issue": "Issue",
    "Discontinued": "CRM Lead",
}
COMBINED_QUEUE_SOURCE = "CRM Lead and Patient"
HTML_TAG_RE = re.compile(r"<[^>]*>")
CONSOLE_SESSION_TTL_SECONDS = 75
CONSOLE_HEARTBEAT_DB_TOUCH_SECONDS = 60
DESK_ACTIVITY_DB_TOUCH_SECONDS = 5 * 60
DESK_ACTIVITY_STALE_SECONDS = DESK_ACTIVITY_DB_TOUCH_SECONDS + CONSOLE_SESSION_TTL_SECONDS
CONSOLE_AVAILABILITY_TOUCH_SECONDS = 60
CONSOLE_STATIC_CONTEXT_TTL_SECONDS = 60
CONSOLE_QUEUE_LIMIT_MAX = 100
ANALYTICS_STATUS_OPTIONS = ("total", "connected", "missed", "busy", "no_answer", "failed", "cancelled")
ANALYTICS_CALL_LIMIT_MAX = 100
# Hard-disabled to prevent the analytics API from executing expensive queries.
ANALYTICS_API_ENABLED = False
AGENT_ATTENDANCE_DOCTYPE = "Vobiz Agent Attendance Log"
AVAILABILITY_ATTENDANCE_SOURCE = "Availability"
ACTIVITY_ATTENDANCE_SOURCES = ("Agent Console", "Desk Activity")
AGENT_SHIFT_START_HOUR = 9
AGENT_SHIFT_END_HOUR = 21
AGENT_SHIFT_MIN_SECONDS = 12 * 60 * 60
AGENT_BREAK_LIMIT_SECONDS = 30 * 60
AGENT_ATTENDANCE_TZ = ZoneInfo("Asia/Kolkata")


def _console_session_key(user: str) -> str:
    return f"vobiz_agent_console:online:{user}"


def _console_tab_key(user: str, tab_id: str) -> str:
    return f"vobiz_agent_console:online:{user}:{tab_id}"


def _console_tabs_key(user: str) -> str:
    return f"vobiz_agent_console:tabs:{user}"


def _console_attendance_key(user: str, shift_date: str) -> str:
    return f"vobiz_agent_console:attendance:{shift_date}:{user}"


def _console_heartbeat_db_touch_key(user: str, tab_id: str) -> str:
    return f"vobiz_agent_console:heartbeat_db_touch:{user}:{tab_id}"


def _console_availability_touch_key(user: str) -> str:
    return f"vobiz_agent_console:availability_touch:{user}"


def _desk_activity_db_touch_key(user: str, tab_id: str) -> str:
    return f"vobiz_agent_console:desk_activity_db_touch:{user}:{tab_id}"


def _console_static_context_key(user: str, queue_source: str, queue_doctype: str, agent_queue_source: str) -> str:
    lang = getattr(frappe.local, "lang", None) or "en"
    return ":".join(
        (
            "vobiz_agent_console",
            "static_context",
            user or "Guest",
            lang,
            queue_source or "",
            queue_doctype or "",
            agent_queue_source or "",
        )
    )


def is_agent_console_online(user: str | None) -> bool:
    if not user:
        return False
    return _active_console_tabs(user) > 0


@frappe.whitelist(methods=["POST"])
def heartbeat_agent_console(tab_id: str | None = None) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    user = frappe.session.user
    tab_id = _clean_tab_id(tab_id)
    now = _now_ist()
    _set_attendance_state(user, True, now)
    if _should_touch_heartbeat_db(user, tab_id):
        _open_or_touch_attendance_session(user, tab_id, now)
    _mark_console_user_available(throttled=True)
    _remember_console_tab(user, tab_id)
    frappe.cache().set_value(
        _console_tab_key(user, tab_id),
        frappe.utils.now(),
        expires_in_sec=CONSOLE_SESSION_TTL_SECONDS,
    )
    frappe.cache().set_value(
        _console_session_key(user),
        frappe.utils.now(),
        expires_in_sec=CONSOLE_SESSION_TTL_SECONDS,
    )
    return {
        "online": True,
        "ttl": CONSOLE_SESSION_TTL_SECONDS,
    }


@frappe.whitelist(methods=["POST"])
def record_agent_activity(tab_id: str | None = None, route: str | None = None) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    user = frappe.session.user
    if not _has_enabled_vobiz_user_mapping(user):
        return {"online": False, "is_mapped": False}

    tab_id = _clean_tab_id(tab_id)
    now = _now_ist()
    _set_attendance_state(user, True, now)
    if _should_touch_desk_activity_db(user, tab_id):
        _open_or_touch_attendance_session(user, tab_id, now, source="Desk Activity")
    _remember_console_tab(user, tab_id)
    frappe.cache().set_value(
        _console_tab_key(user, tab_id),
        route or frappe.utils.now(),
        expires_in_sec=CONSOLE_SESSION_TTL_SECONDS,
    )
    frappe.cache().set_value(
        _console_session_key(user),
        frappe.utils.now(),
        expires_in_sec=CONSOLE_SESSION_TTL_SECONDS,
    )
    return {
        "online": True,
        "is_mapped": True,
        "ttl": CONSOLE_SESSION_TTL_SECONDS,
    }


@frappe.whitelist(methods=["GET", "POST"])
def mark_agent_activity_inactive(tab_id: str | None = None) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        return {"online": False, "is_mapped": False}

    user = frappe.session.user
    if not _has_enabled_vobiz_user_mapping(user):
        return {"online": False, "is_mapped": False}
    idle_auto_offline = get_idle_auto_offline_config()
    if not idle_auto_offline.get("enabled"):
        return {"online": is_agent_console_online(user), "is_mapped": True, "idle_auto_offline_enabled": False}
    if not _should_apply_idle_auto_offline(user):
        return {"online": is_agent_console_online(user), "is_mapped": True, "idle_auto_offline_enabled": False}

    tab_id = _clean_tab_id(tab_id)
    frappe.cache().delete_value(_console_tab_key(user, tab_id))
    _forget_console_tab(user, tab_id)
    _close_attendance_session(user, tab_id, _now_ist())
    still_online = is_agent_console_online(user)
    if not still_online:
        frappe.cache().delete_value(_console_session_key(user))
        _close_all_activity_sessions(user, _now_ist())
        _set_attendance_state(user, False, _now_ist())
        mapping_name = frappe.db.get_value("Vobiz User Mapping", {"user": user, "enabled": 1}, "name")
        if mapping_name:
            mapping = frappe.db.get_value(
                "Vobiz User Mapping",
                mapping_name,
                ["availability_status", "current_call_log"],
                as_dict=True,
            ) or {}
            active = False
            current_call_log = mapping.get("current_call_log")
            if current_call_log and frappe.db.exists("Vobiz Call Log", current_call_log):
                active = is_active_call_log(current_call_log)
            if (mapping.get("availability_status") or "Available") != "Away" and not active:
                frappe.db.set_value(
                    "Vobiz User Mapping",
                    mapping_name,
                    {
                        "availability_status": "Offline",
                        "accept_calls": 0,
                        "current_call_log": "",
                        "last_status_at": frappe.utils.now(),
                    },
                    update_modified=True,
                )
                _record_availability_attendance(user, "Offline")
                frappe.db.commit()
    return {"online": still_online, "is_mapped": True}


@frappe.whitelist(methods=["GET", "POST"])
def mark_agent_console_offline(tab_id: str | None = None) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        return {"online": False}

    user = frappe.session.user
    tab_id = _clean_tab_id(tab_id)
    frappe.cache().delete_value(_console_tab_key(user, tab_id))
    _forget_console_tab(user, tab_id)
    _close_attendance_session(user, tab_id, _now_ist())
    still_online = is_agent_console_online(user)
    if still_online:
        return {"online": True}

    frappe.cache().delete_value(_console_session_key(user))
    _set_attendance_state(user, False, _now_ist())
    mapping_name = frappe.db.get_value("Vobiz User Mapping", {"user": frappe.session.user, "enabled": 1}, "name")
    if mapping_name:
        current_call_log = frappe.db.get_value("Vobiz User Mapping", mapping_name, "current_call_log")
        active = False
        if current_call_log and frappe.db.exists("Vobiz Call Log", current_call_log):
            active = is_active_call_log(current_call_log)
        if not active:
            frappe.db.set_value(
                "Vobiz User Mapping",
                mapping_name,
                {
                    "availability_status": "Offline",
                    "accept_calls": 0,
                    "current_call_log": "",
                    "last_status_at": frappe.utils.now(),
                },
                update_modified=True,
            )
            frappe.db.commit()
    return {"online": False}


@frappe.whitelist()
def get_agent_console_data(
    limit: int | str = 25,
    search: str | None = None,
    followup_day: str | None = None,
    queue_source_filter: str | None = None,
    sort_by: str | None = None,
    filters: str | list | None = None,
) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    limit = max(5, min(frappe.utils.cint(limit) or 25, CONSOLE_QUEUE_LIMIT_MAX))
    agent = _agent_context()
    agent_queue_source = _agent_queue_source(agent)
    queue_source = _selected_queue_source(agent_queue_source, queue_source_filter)
    queue_doctype = _queue_doctype_for_source(queue_source)
    static_context = _get_console_static_context(queue_source, queue_doctype, agent_queue_source)
    return {
        "availability": get_call_capability(),
        "active_call": _active_call(),
        "queue": _lead_queue(
            limit,
            search,
            agent=agent,
            queue_source=queue_source,
            followup_day=followup_day,
            sort_by=sort_by,
            user_filters=filters,
        ),
        "queue_meta": static_context["queue_meta"],
        "dispositions": static_context["dispositions"],
        "ai_disposition_enabled": static_context["ai_disposition_enabled"],
    }


def _get_console_static_context(queue_source: str, queue_doctype: str, agent_queue_source: str) -> dict[str, Any]:
    cache_key = _console_static_context_key(
        frappe.session.user,
        queue_source,
        queue_doctype,
        agent_queue_source,
    )
    try:
        cached = frappe.cache().get_value(cache_key)
        if cached is not None:
            return cached
    except Exception:
        cached = None

    settings = get_settings()
    context = {
        "queue_meta": _queue_meta(queue_source, queue_doctype, agent_queue_source=agent_queue_source),
        "dispositions": get_disposition_options_api(),
        "patient_followup_status_options": get_patient_followup_status_options_api(),
        "ai_disposition_enabled": bool(settings.enable_ai_disposition),
    }
    try:
        frappe.cache().set_value(cache_key, context, expires_in_sec=CONSOLE_STATIC_CONTEXT_TTL_SECONDS)
    except Exception:
        pass
    return context


def _mark_console_user_available(throttled: bool = False) -> None:
    mapping_name = frappe.db.get_value("Vobiz User Mapping", {"user": frappe.session.user, "enabled": 1}, "name")
    if not mapping_name:
        return
    mapping = frappe.db.get_value(
        "Vobiz User Mapping",
        mapping_name,
        ["availability_status", "accept_calls", "current_call_log"],
        as_dict=True,
    )
    current_call_log = mapping.get("current_call_log")
    if current_call_log and frappe.db.exists("Vobiz Call Log", current_call_log) and is_active_call_log(current_call_log):
        return
    if mapping.get("availability_status") in {"Away", "Offline"}:
        return
    if (
        mapping.get("availability_status") == "Available"
        and frappe.utils.cint(mapping.get("accept_calls"))
        and not current_call_log
    ):
        return
    if throttled and not _should_touch_availability(frappe.session.user):
        return
    updates = {
        "availability_status": "Available",
        "accept_calls": 1,
        "current_call_log": "",
    }
    if mapping.get("availability_status") != "Available" or not frappe.utils.cint(mapping.get("accept_calls")):
        updates["last_status_at"] = frappe.utils.now()
    frappe.db.set_value(
        "Vobiz User Mapping",
        mapping_name,
        updates,
        update_modified=True,
    )
    _record_availability_attendance(frappe.session.user, "Available")
    frappe.db.commit()


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


@frappe.whitelist(methods=["POST"])
def save_reference_note(reference_doctype: str, reference_name: str, note: str) -> dict[str, Any]:
    doc = _get_permitted_reference(reference_doctype, reference_name)
    note = (note or "").strip()
    if not note:
        frappe.throw(_("Note is required."))
    doc.add_comment("Comment", note)
    frappe.db.commit()
    return {"success": True, "reference_doctype": reference_doctype, "reference_name": reference_name}


@frappe.whitelist(methods=["POST"])
def update_reference_status(reference_doctype: str, reference_name: str, status: str) -> dict[str, Any]:
    if reference_doctype not in {"Patient Encounter", "Issue"}:
        frappe.throw(_("Status update is not enabled for this DocType."))
    doc = _get_permitted_reference(reference_doctype, reference_name)
    if not doc.has_permission("write") and not _has_mapped_queue_access(reference_doctype):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    status = (status or "").strip()
    options = _status_options(reference_doctype)
    if not status or status not in options:
        frappe.throw(_("Select a valid status."))

    frappe.db.set_value(reference_doctype, reference_name, "status", status, update_modified=True)
    frappe.db.commit()
    return {"success": True, "status": status}


def _get_permitted_reference(reference_doctype: str, reference_name: str):
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if not reference_doctype or not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        frappe.throw(_("Reference not found."))

    doc = frappe.get_doc(reference_doctype, reference_name)
    if not doc.has_permission("read") and not _has_mapped_patient_access(reference_doctype, reference_name):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    return doc


def _has_mapped_queue_access(reference_doctype: str) -> bool:
    agent = _agent_context()
    queue_source = (agent.get("queue_source") or "").strip()
    return QUEUE_SOURCE_DOCTYPES.get(queue_source) == reference_doctype


def _has_mapped_patient_access(reference_doctype: str, reference_name: str) -> bool:
    if reference_doctype != "Patient":
        return False
    agent = _agent_context()
    if (agent.get("queue_source") or "").strip() not in {"Patient", COMBINED_QUEUE_SOURCE}:
        return False
    meta = frappe.get_meta("Patient")
    filters: dict[str, Any] = {"name": reference_name}
    if meta.has_field("sr_medical_department"):
        departments = _split_values(agent.get("sr_medical_departments"), first=agent.get("sr_medical_department"))
        if not departments:
            return False
        filters["sr_medical_department"] = ["in", departments]
    if meta.has_field("sr_followup_id"):
        followup_ids = _split_values(agent.get("sr_followup_ids"), first=agent.get("sr_followup_id"))
        if not followup_ids:
            return False
        filters["sr_followup_id"] = ["in", followup_ids]
    return bool(frappe.db.exists("Patient", filters))


@frappe.whitelist()
def get_whatsapp_conversation(reference_doctype: str, reference_name: str) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if not reference_doctype or not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        frappe.throw(_("Reference not found."))

    conversation = _conversation_for_reference_phone(reference_doctype, reference_name)
    if conversation:
        return {"success": True, "conversation": conversation}

    route_status = _whatsapp_route_status(reference_doctype, reference_name)
    if not route_status.get("available"):
        return route_status

    if frappe.db.exists("DocType", "Chat Conversation"):
        try:
            from wa_chat_hub.api.chat import get_conversation_for_reference

            result = get_conversation_for_reference(
                reference_doctype,
                reference_name,
                channel_account=route_status.get("channel_account"),
            ) or {}
            if result.get("success") and result.get("conversation"):
                return {
                    "success": True,
                    "conversation": result.get("conversation"),
                    "created": bool(result.get("created")),
                }
            if result.get("message"):
                return {
                    "success": False,
                    "conversation": None,
                    "message": result.get("message"),
                }
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Vobiz Workdesk WhatsApp Conversation Create Failed")
            raise

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
def get_whatsapp_messages(conversation: str, limit: int | str = 30, before: str | None = None) -> dict[str, Any]:
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
    return {"success": True, "result": {**outbound, **result}, **_whatsapp_messages_page(conversation, 30)}


@frappe.whitelist()
def get_whatsapp_templates(conversation: str, force_refresh: int | str = 0) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    _ensure_whatsapp_conversation_read(conversation)

    try:
        from wa_chat_hub.api.runtime import get_interakt_templates

        response = get_interakt_templates(conversation=conversation, force_refresh=force_refresh)
        result = response.get("result") or {}
        return {
            "success": True,
            "templates": result.get("templates") or [],
            "channel_account": result.get("channel_account"),
            "count": result.get("count") or 0,
        }
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "Vobiz Workdesk WhatsApp Templates Failed")
        return {"success": False, "templates": [], "message": str(exc)}


@frappe.whitelist(methods=["POST"])
def send_whatsapp_template(
    conversation: str,
    template_name: str,
    language_code: str | None = None,
    body_values: str | list | None = None,
    header_values: str | list | None = None,
    followup_body: str | None = None,
    body_preview: str | None = None,
    template_category: str | None = None,
) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if not (template_name or "").strip():
        frappe.throw(_("Template is required."))
    _ensure_whatsapp_conversation_read(conversation)

    from wa_chat_hub.outbound import send_interakt_template_message
    from wa_chat_hub.services import append_message

    template = {
        "template_name": template_name.strip(),
        "language_code": (language_code or "").strip() or None,
        "body_values": _list_from_template_values(body_values),
        "header_values": _list_from_template_values(header_values),
        "template_category": template_category or "",
    }
    try:
        outbound = send_interakt_template_message(conversation, template)
        delivery_status = outbound.get("delivery_status") or "Sent"
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "Vobiz Workdesk WhatsApp Template Send Failed")
        outbound = {
            "conversation": conversation,
            "sent": False,
            "delivery_status": "Failed",
            "template_name": template_name,
            "error": str(exc),
        }
        delivery_status = "Failed"

    convo = frappe.get_doc("Chat Conversation", conversation)
    contact_phone = frappe.db.get_value("Chat Contact", convo.contact, "phone_number")
    body_text = (body_preview or "").strip() or _("Template: {0}").format(template_name)
    result = append_message({
        "channel_account": convo.channel_account,
        "phone_number": contact_phone,
        "direction": "Outbound",
        "sender_type": "Agent",
        "content_type": "Template",
        "body": body_text,
        "delivery_status": delivery_status,
        "channel_message_id": outbound.get("provider_message_id"),
        "raw_transport_payload": outbound,
        "template_category": template_category,
    })

    followup_body = (followup_body or "").strip()
    if followup_body:
        send_whatsapp_reply(conversation, followup_body)
    else:
        frappe.db.commit()
    return {"success": True, "result": {**outbound, **result}, **_whatsapp_messages_page(conversation, 30)}


@frappe.whitelist()
def get_active_call() -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    return _active_call() or {}


@frappe.whitelist()
def get_call_performance(
    status_filter: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    queue_source: str | None = None,
    agent_user: str | None = None,
    lead_owner: str | None = None,
    team: str | None = None,
    department: str | None = None,
) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    agent = _agent_context()
    data = _analytics_data(
        from_date=from_date,
        to_date=to_date,
        status_filter=status_filter,
        queue_source=queue_source or _agent_queue_source(agent),
        agent_user=agent_user,
        lead_owner=lead_owner,
        team=team,
        department=department,
        agent=agent,
        include_calls=1,
        call_limit=50,
    )
    return {
        "is_admin": data.get("is_admin"),
        "status_filter": data.get("status_filter"),
        "summary": data.get("filtered_summary"),
        "overall": data.get("summary"),
        "agents": data.get("agents"),
        "calls": data.get("calls"),
        "from_date": data.get("from_date"),
        "to_date": data.get("to_date"),
        "queue_source": data.get("queue_source"),
        "lead_owner": data.get("lead_owner"),
        "team": data.get("team"),
        "department": data.get("department"),
    }


@frappe.whitelist()
def get_analytics(
    from_date: str | None = None,
    to_date: str | None = None,
    status_filter: str | None = None,
    queue_source: str | None = None,
    agent_user: str | None = None,
    lead_owner: str | None = None,
    team: str | None = None,
    department: str | None = None,
    include_calls: int | str = 0,
    call_limit: int | str = 50,
    call_offset: int | str = 0,
    unique_only: int | str = 0,
) -> dict[str, Any]:
    if not ANALYTICS_API_ENABLED or frappe.conf.get("disable_vobiz_analytics_api"):
        frappe.throw(_("Vobiz analytics API is temporarily disabled."), frappe.PermissionError)

    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    agent = _agent_context()
    return _analytics_data(
        from_date=from_date,
        to_date=to_date,
        status_filter=status_filter,
        queue_source=queue_source or _agent_queue_source(agent),
        agent_user=agent_user,
        lead_owner=lead_owner,
        team=team,
        department=department,
        agent=agent,
        include_calls=include_calls,
        call_limit=call_limit,
        call_offset=call_offset,
        unique_only=unique_only,
    )


def _call_summary() -> dict[str, Any]:
    return _analytics_data().get("summary", {})


def _analytics_data(
    from_date: str | None = None,
    to_date: str | None = None,
    status_filter: str | None = None,
    queue_source: str | None = None,
    agent_user: str | None = None,
    lead_owner: str | None = None,
    team: str | None = None,
    department: str | None = None,
    agent: dict[str, Any] | None = None,
    include_calls: int | str = 0,
    call_limit: int | str = 50,
    call_offset: int | str = 0,
    unique_only: int | str = 0,
) -> dict[str, Any]:
    from_date, to_date = _analytics_date_range(from_date, to_date)
    status_filter = _analytics_status_filter(status_filter)
    queue_source = queue_source if queue_source in QUEUE_SOURCE_DOCTYPES else _agent_queue_source(agent)
    start = f"{from_date} 00:00:00"
    end = f"{to_date} 23:59:59"
    filters: dict[str, Any] = {"creation": ["between", [start, end]]}
    if queue_source == "Patient":
        filters["reference_doctype"] = "Patient"
    elif queue_source in {"CRM Lead", "Discontinued"}:
        filters["reference_doctype"] = "CRM Lead"

    is_admin = _can_view_all_analytics_agents()
    is_crm_lead_queue = queue_source in {"CRM Lead", "Discontinued"}
    is_patient_queue = queue_source == "Patient"
    visible_crm_leads: list[str] | None = None
    if not is_admin and queue_source in {"CRM Lead", "Discontinued", COMBINED_QUEUE_SOURCE}:
        _apply_visible_crm_lead_scope(filters)
        visible_crm_leads = list(filters.get("reference_names") or filters.get("crm_lead_reference_names") or [])

    team_scope = [] if is_admin else _team_member_users_for_leader(frappe.session.user)
    agent_user_values = _analytics_filter_values(agent_user)
    agent_user = agent_user_values[0] if len(agent_user_values) == 1 else ""
    lead_owner_values = _analytics_filter_values(lead_owner)
    team_values = _analytics_filter_values(team)
    lead_owner = lead_owner_values[0] if len(lead_owner_values) == 1 else ""
    team = team_values[0] if len(team_values) == 1 else ""
    department = (department or "").strip()
    if is_crm_lead_queue:
        _apply_crm_lead_analytics_filters(
            filters,
            lead_owner=lead_owner_values,
            team=team_values,
            visible_leads=visible_crm_leads,
        )
    elif is_patient_queue:
        _apply_patient_department_analytics_filter(filters, department=department)

    if is_admin and agent_user_values:
        filters["users"] = agent_user_values
    elif team_scope:
        scoped_agent_users = [user for user in agent_user_values if user in team_scope]
        if scoped_agent_users:
            filters["users"] = scoped_agent_users
        elif agent_user and agent_user in team_scope:
            filters["user"] = agent_user
        else:
            filters["users"] = team_scope
    elif not is_admin:
        filters["user"] = frappe.session.user
    conditions, params = _analytics_sql_conditions(start, end, filters)

    meta = frappe.get_meta("Vobiz Call Log")
    fields = ["name", "user", "status", "duration", "billsec", "creation"]
    fields.extend(
        field
        for field in _existing_fields(
            meta,
            (
                "call_status",
                "dial_status",
                "hangup_cause",
                "direction",
                "reference_doctype",
                "reference_name",
                "customer_number",
                "user_mobile",
                "caller_id",
                "disposition",
                "cost",
                "call_flow",
                "answer_time",
                "end_time",
                "recording_status",
                "recording_url",
            ),
        )
        if field not in fields
    )
    summary = _analytics_summary_sql(conditions, params)
    filtered_summary = _analytics_summary_sql(conditions, params, status_filter=status_filter)
    include_call_rows = bool(frappe.utils.cint(include_calls))
    unique_call_rows = bool(frappe.utils.cint(unique_only))
    call_limit = max(10, min(frappe.utils.cint(call_limit) or 50, ANALYTICS_CALL_LIMIT_MAX))
    call_offset = max(0, frappe.utils.cint(call_offset) or 0)
    call_slice = (
        _analytics_call_rows_sql(
            conditions,
            params,
            fields,
            status_filter=status_filter,
            limit=call_limit,
            offset=call_offset,
            queue_source=queue_source,
            unique_only=unique_call_rows,
        )
        if include_call_rows
        else []
    )
    matching_call_count = filtered_summary.get("unique_calls" if unique_call_rows else "total", 0)

    return {
        "from_date": from_date,
        "to_date": to_date,
        "status_filter": status_filter,
        "queue_source": queue_source,
        "queue_sources": list(QUEUE_SOURCE_DOCTYPES.keys()),
        "agent_user": filters.get("users") or ([filters.get("user")] if filters.get("user") else []),
        "lead_owner": lead_owner_values if is_crm_lead_queue else [],
        "team": team_values if is_crm_lead_queue else [],
        "department": department if is_patient_queue else "",
        "team_options": _analytics_team_options(queue_source, visible_leads=visible_crm_leads),
        "lead_owner_options": _analytics_lead_owner_options(queue_source, team=team_values, visible_leads=visible_crm_leads),
        "department_options": _analytics_department_options(queue_source),
        "agent_options": _analytics_agent_options(
            is_admin,
            team_scope=team_scope,
            queue_source=queue_source,
            team=team,
            visible_leads=visible_crm_leads,
        ),
        "is_admin": is_admin,
        "is_team_leader": bool(team_scope),
        "summary": summary,
        "filtered_summary": filtered_summary,
        "status_breakdown": _analytics_status_breakdown_sql(conditions, params),
        "outcome_breakdown": _analytics_outcome_breakdown_sql(conditions, params),
        "daily": _analytics_daily_sql(conditions, params, from_date, to_date),
        "agents": _analytics_agents_sql(
            conditions,
            params,
            status_filter=status_filter,
            queue_source=queue_source,
            agent_user=agent_user_values,
            lead_owner=lead_owner_values,
            team_scope=team_scope,
        ),
        "calls": [_performance_call_row(row) for row in call_slice],
        "calls_loaded": include_call_rows,
        "call_limit": call_limit,
        "call_offset": call_offset,
        "has_more_calls": include_call_rows and (call_offset + call_limit) < matching_call_count,
        "matching_call_count": matching_call_count,
    }


def _analytics_sql_conditions(start: str, end: str, filters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {"start": start, "end": end}
    conditions = ["`creation` between %(start)s and %(end)s"]
    if filters.get("reference_doctype"):
        params["reference_doctype"] = filters["reference_doctype"]
        conditions.append("`reference_doctype` = %(reference_doctype)s")
    if filters.get("reference_names"):
        params["reference_names"] = tuple(filters["reference_names"])
        conditions.append("`reference_name` in %(reference_names)s")
    if filters.get("crm_lead_reference_names"):
        params["crm_lead_reference_names"] = tuple(filters["crm_lead_reference_names"])
        conditions.append("(`reference_doctype` != 'CRM Lead' or `reference_name` in %(crm_lead_reference_names)s)")
    if filters.get("exclude_crm_leads"):
        conditions.append("`reference_doctype` != 'CRM Lead'")
    if filters.get("force_empty"):
        conditions.append("1 = 0")
    if filters.get("user"):
        params["user"] = filters["user"]
        conditions.append("`user` = %(user)s")
    if filters.get("users"):
        users = [user for user in filters["users"] if user]
        if users:
            params["users"] = tuple(users)
            conditions.append("`user` in %(users)s")
    return " and ".join(conditions), params


def _apply_crm_lead_analytics_filters(
    filters: dict[str, Any],
    *,
    lead_owner: str | list[str] | None = None,
    team: str | list[str] | None = None,
    visible_leads: list[str] | None = None,
) -> None:
    if not frappe.db.exists("DocType", "CRM Lead"):
        filters["force_empty"] = True
        return

    lead_owners = _analytics_filter_values(lead_owner)
    teams = _analytics_filter_values(team)
    filters["lead_owner"] = lead_owners
    filters["team"] = teams
    if not lead_owners and not teams:
        return

    lead_filters: dict[str, Any] = {}
    meta = frappe.get_meta("CRM Lead")
    if lead_owners and not meta.has_field("lead_owner"):
        filters["force_empty"] = True
        return
    if teams and not meta.has_field("team"):
        filters["force_empty"] = True
        return
    if lead_owners:
        lead_filters["lead_owner"] = ["in", lead_owners] if len(lead_owners) > 1 else lead_owners[0]
    if teams:
        lead_filters["team"] = ["in", teams] if len(teams) > 1 else teams[0]
    if visible_leads is not None:
        if visible_leads:
            lead_filters["name"] = ["in", visible_leads]
        else:
            filters["force_empty"] = True
            return

    names = frappe.get_all(
        "CRM Lead",
        filters=lead_filters,
        pluck="name",
        limit_page_length=50000,
    )
    if names:
        filters["reference_names"] = names
    else:
        filters["force_empty"] = True


def _apply_patient_department_analytics_filter(
    filters: dict[str, Any],
    *,
    department: str | None = None,
) -> None:
    department = (department or "").strip()
    filters["department"] = department
    if not department:
        return
    if not frappe.db.exists("DocType", "Patient"):
        filters["force_empty"] = True
        return
    meta = frappe.get_meta("Patient")
    if not meta.has_field("sr_medical_department"):
        filters["force_empty"] = True
        return

    names = frappe.get_all(
        "Patient",
        filters={"sr_medical_department": department},
        pluck="name",
        limit_page_length=50000,
    )
    if names:
        filters["reference_names"] = names
    else:
        filters["force_empty"] = True


def _analytics_bucket_sql() -> str:
    signal = "lower(concat_ws(' ', coalesce(`status`, ''), coalesce(`call_status`, ''), coalesce(`dial_status`, ''), coalesce(`hangup_cause`, '')))"
    recording = _analytics_recording_duration_sql()
    return f"""
        case
            when {recording} > 0 or coalesce(`billsec`, 0) > 0 then 'connected'
            when {signal} like '%%busy%%' then 'busy'
            when {signal} like '%%no-answer%%' or {signal} like '%%no answer%%' or {signal} like '%%timeout%%' or {signal} like '%%unanswered%%' then 'no_answer'
            when `status` in ('Connected', 'Completed') then 'no_answer'
            when {signal} like '%%cancel%%' or {signal} like '%%reject%%' or {signal} like '%%decline%%' then 'cancelled'
            when {signal} like '%%fail%%' or {signal} like '%%error%%' then 'failed'
            when `status` in ('Failed', 'Busy', 'No Answer', 'Cancelled', 'Canceled') then 'missed'
            else 'other'
        end
    """


def _analytics_recording_duration_sql() -> str:
    return """
        case
            when coalesce(`recording_duration`, 0) > 3600 then round(coalesce(`recording_duration`, 0) / 1000)
            else coalesce(`recording_duration`, 0)
        end
    """


def _analytics_talk_sql() -> str:
    customer_answer_duration = """
        case
            when `call_flow` = 'Customer First'
                and `answer_time` is not null
                and `end_time` is not null
                and timestampdiff(second, `answer_time`, `end_time`) > 0
            then timestampdiff(second, `answer_time`, `end_time`)
            else 0
        end
    """
    agent_first_customer_duration = """
        case
            when `call_flow` = 'Agent First' and coalesce(`duration`, 0) > 0 then coalesce(`duration`, 0)
            else 0
        end
    """
    return f"coalesce(nullif({customer_answer_duration}, 0), nullif({agent_first_customer_duration}, 0), 0)"


def _analytics_unique_key_sql() -> str:
    return """
        case
            when coalesce(nullif(`reference_name`, ''), '') != '' then concat(coalesce(nullif(`reference_doctype`, ''), 'Reference'), ':', `reference_name`)
            when coalesce(nullif(`customer_number`, ''), '') != '' then concat('Phone:', `customer_number`)
            else `name`
        end
    """


def _analytics_column(name: str, table_alias: str | None = None) -> str:
    return f"{table_alias}.`{name}`" if table_alias else f"`{name}`"


def _analytics_bucket_column(table_alias: str | None = None) -> str:
    return f"{table_alias}.bucket" if table_alias else "bucket"


def _analytics_bucket_filter_sql(status_filter: str | None, table_alias: str | None = None) -> str:
    status_filter = _analytics_status_filter(status_filter)
    bucket = _analytics_bucket_column(table_alias)
    direction = _analytics_column("direction", table_alias)
    if status_filter == "connected":
        return f"where {bucket} = 'connected'"
    if status_filter == "missed":
        return f"where {bucket} in ('missed', 'busy', 'no_answer', 'failed', 'cancelled') and {direction} = 'Incoming'"
    if status_filter in {"busy", "no_answer", "failed", "cancelled"}:
        return f"where {bucket} = '{status_filter}'"
    return ""


def _analytics_summary_sql(conditions: str, params: dict[str, Any], status_filter: str | None = None) -> dict[str, Any]:
    bucket_expr = _analytics_bucket_sql()
    bucket_filter = _analytics_bucket_filter_sql(status_filter)
    unique_key_expr = _analytics_unique_key_sql()
    rows = frappe.db.sql(
        f"""
        select bucket, `direction`, count(*) as call_count, sum(talk_seconds) as talk_seconds, sum(cost) as cost
        from (
            select {bucket_expr} as bucket, `direction`, {_analytics_talk_sql()} as talk_seconds, coalesce(`cost`, 0) as cost
            from `tabVobiz Call Log`
            where {conditions}
        ) analytics
        {bucket_filter}
        group by bucket, `direction`
        """,
        params,
        as_dict=True,
    )
    unique_rows = frappe.db.sql(
        f"""
        select count(distinct unique_key) as unique_calls
        from (
            select {bucket_expr} as bucket, `direction`, {unique_key_expr} as unique_key
            from `tabVobiz Call Log`
            where {conditions}
        ) analytics
        {bucket_filter}
        """,
        params,
        as_dict=True,
    )
    unique_calls = frappe.utils.cint(unique_rows[0].unique_calls) if unique_rows else 0
    return _summary_from_bucket_rows(rows, unique_calls=unique_calls)


def _summary_from_bucket_rows(rows: list[dict[str, Any]], unique_calls: int = 0) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.bucket] = counts.get(row.bucket, 0) + frappe.utils.cint(row.call_count)
    total = sum(counts.values())
    connected = counts.get("connected", 0)
    missed = sum(
        frappe.utils.cint(row.call_count)
        for row in rows
        if row.bucket in {"missed", "busy", "no_answer", "failed", "cancelled"}
        and (row.get("direction") in (None, "", "Incoming"))
    )
    talk_seconds = sum(
        frappe.utils.cint(row.talk_seconds)
        for row in rows
        if row.bucket == "connected"
    )
    average_duration = round(talk_seconds / connected) if connected else 0
    return {
        "total": total,
        "unique_calls": frappe.utils.cint(unique_calls),
        "connected": connected,
        "missed": missed,
        "busy": counts.get("busy", 0),
        "no_answer": counts.get("no_answer", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
        "rejected": counts.get("cancelled", 0),
        "talk_seconds": talk_seconds,
        "talk_time_label": _duration_label(talk_seconds),
        "average_duration": average_duration,
        "average_duration_label": _duration_label(average_duration),
        "answer_rate": round((connected / total) * 100, 1) if total else 0,
        "missed_rate": round((missed / total) * 100, 1) if total else 0,
        "cost": round(sum(frappe.utils.flt(row.cost) for row in rows), 2),
    }


def _analytics_status_breakdown_sql(conditions: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    summary = _analytics_summary_sql(conditions, params)
    data = [
        {"bucket": "connected", "label": _("Connected"), "count": summary.get("connected") or 0},
        {"bucket": "missed", "label": _("Missed"), "count": summary.get("missed") or 0},
    ]
    other = next((row.get("count") for row in _analytics_outcome_breakdown_sql(conditions, params) if row.get("bucket") == "other"), 0)
    if other:
        data.append({"bucket": "other", "label": _("Other"), "count": other})
    return [row for row in data if row["count"]]


def _analytics_outcome_breakdown_sql(conditions: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    bucket_expr = _analytics_bucket_sql()
    rows = frappe.db.sql(
        f"""
        select bucket, count(*) as call_count, sum(talk_seconds) as talk_seconds, sum(cost) as cost
        from (
            select {bucket_expr} as bucket, {_analytics_talk_sql()} as talk_seconds, coalesce(`cost`, 0) as cost
            from `tabVobiz Call Log`
            where {conditions}
        ) analytics
        group by bucket
        """,
        params,
        as_dict=True,
    )
    by_bucket = {row.bucket: row for row in rows}
    data = []
    for bucket in ["connected", "busy", "no_answer", "failed", "cancelled", "missed", "other"]:
        row = by_bucket.get(bucket)
        if not row:
            continue
        summary = _summary_from_bucket_rows([row])
        data.append(
            {
                "bucket": bucket,
                "label": _analytics_bucket_label(bucket),
                "count": frappe.utils.cint(row.call_count),
                "average_duration_label": summary.get("average_duration_label"),
            }
        )
    return data


def _analytics_daily_sql(conditions: str, params: dict[str, Any], from_date: str, to_date: str) -> list[dict[str, Any]]:
    bucket_expr = _analytics_bucket_sql()
    unique_key_expr = _analytics_unique_key_sql()
    rows = frappe.db.sql(
        f"""
        select call_date, bucket, `direction`, count(*) as call_count, sum(talk_seconds) as talk_seconds, sum(cost) as cost
        from (
            select date(`creation`) as call_date, {bucket_expr} as bucket, `direction`, {_analytics_talk_sql()} as talk_seconds, coalesce(`cost`, 0) as cost
            from `tabVobiz Call Log`
            where {conditions}
        ) analytics
        group by call_date, bucket, `direction`
        """,
        params,
        as_dict=True,
    )
    unique_rows = frappe.db.sql(
        f"""
        select call_date, count(distinct unique_key) as unique_calls
        from (
            select date(`creation`) as call_date, {unique_key_expr} as unique_key
            from `tabVobiz Call Log`
            where {conditions}
        ) analytics
        group by call_date
        """,
        params,
        as_dict=True,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.call_date), []).append(row)
    unique_by_day = {str(row.call_date): frappe.utils.cint(row.unique_calls) for row in unique_rows}

    start = frappe.utils.getdate(from_date)
    end = frappe.utils.getdate(to_date)
    days = []
    current = start
    while current <= end:
        day = str(current)
        days.append({"date": day, **_summary_from_bucket_rows(grouped.get(day, []), unique_calls=unique_by_day.get(day, 0))})
        current = frappe.utils.add_days(current, 1)
    return days


def _analytics_agents_sql(
    conditions: str,
    params: dict[str, Any],
    status_filter: str | None = None,
    queue_source: str | None = None,
    agent_user: str | list[str] | None = None,
    lead_owner: str | None = None,
    team_scope: list[str] | None = None,
) -> list[dict[str, Any]]:
    bucket_expr = _analytics_bucket_sql()
    bucket_filter = _analytics_bucket_filter_sql(status_filter)
    unique_key_expr = _analytics_unique_key_sql()
    if queue_source in {"CRM Lead", "Discontinued"} and frappe.db.exists("DocType", "CRM Lead"):
        joined_bucket_filter = _analytics_bucket_filter_sql(status_filter, "analytics")
        rows = frappe.db.sql(
            f"""
            select coalesce(nullif(lead.`lead_owner`, ''), 'Unassigned') as agent, analytics.bucket, analytics.`direction`, count(*) as call_count, sum(analytics.talk_seconds) as talk_seconds, sum(analytics.cost) as cost
            from (
                select `reference_name`, {bucket_expr} as bucket, `direction`, {_analytics_talk_sql()} as talk_seconds, coalesce(`cost`, 0) as cost
                from `tabVobiz Call Log`
                where {conditions}
            ) analytics
            left join `tabCRM Lead` lead on lead.`name` = analytics.`reference_name`
            {joined_bucket_filter}
            group by agent, analytics.bucket, analytics.`direction`
            """,
            params,
            as_dict=True,
        )
        unique_rows = frappe.db.sql(
            f"""
            select coalesce(nullif(lead.`lead_owner`, ''), 'Unassigned') as agent, count(distinct analytics.unique_key) as unique_calls
            from (
                select `reference_name`, {bucket_expr} as bucket, `direction`, {unique_key_expr} as unique_key
                from `tabVobiz Call Log`
                where {conditions}
            ) analytics
            left join `tabCRM Lead` lead on lead.`name` = analytics.`reference_name`
            {joined_bucket_filter}
            group by agent
            """,
            params,
            as_dict=True,
        )
    else:
        rows = frappe.db.sql(
            f"""
            select agent, bucket, `direction`, count(*) as call_count, sum(talk_seconds) as talk_seconds, sum(cost) as cost
            from (
                select coalesce(nullif(`user`, ''), 'Unassigned') as agent, {bucket_expr} as bucket, `direction`, {_analytics_talk_sql()} as talk_seconds, coalesce(`cost`, 0) as cost
                from `tabVobiz Call Log`
                where {conditions}
            ) analytics
            {bucket_filter}
            group by agent, bucket, `direction`
            """,
            params,
            as_dict=True,
        )
        unique_rows = frappe.db.sql(
            f"""
            select agent, count(distinct unique_key) as unique_calls
            from (
                select coalesce(nullif(`user`, ''), 'Unassigned') as agent, {bucket_expr} as bucket, `direction`, {unique_key_expr} as unique_key
                from `tabVobiz Call Log`
                where {conditions}
            ) analytics
            {bucket_filter}
            group by agent
            """,
            params,
            as_dict=True,
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.agent or _("Unassigned"), []).append(row)
    unique_by_agent = {row.agent or _("Unassigned"): frappe.utils.cint(row.unique_calls) for row in unique_rows}
    data = [
        {"user": user, **_summary_from_bucket_rows(user_rows, unique_calls=unique_by_agent.get(user, 0))}
        for user, user_rows in grouped.items()
    ]
    _append_mapped_agents_with_zero_calls(
        data,
        queue_source=queue_source,
        agent_user=agent_user,
        lead_owner=lead_owner,
        team_scope=team_scope,
    )
    _attach_agent_availability(data)
    return sorted(data, key=lambda row: row["total"], reverse=True)


def _append_mapped_agents_with_zero_calls(
    rows: list[dict[str, Any]],
    *,
    queue_source: str | None = None,
    agent_user: str | list[str] | None = None,
    lead_owner: str | list[str] | None = None,
    team_scope: list[str] | None = None,
) -> None:
    if not frappe.db.exists("DocType", "Vobiz User Mapping"):
        return

    filters: dict[str, Any] = {"enabled": 1}
    queue_filter = _analytics_mapped_agent_queue_filter(queue_source)
    if queue_filter:
        filters["queue_source"] = ["in", queue_filter]
    users = _analytics_mapped_agent_scope(
        queue_source=queue_source,
        agent_user=agent_user,
        lead_owner=lead_owner,
        team_scope=team_scope,
    )
    if users is not None:
        if not users:
            return
        filters["user"] = ["in", sorted(users)]

    mapped_users = frappe.get_all(
        "Vobiz User Mapping",
        filters=filters,
        pluck="user",
        limit_page_length=50000,
    )
    existing = {row.get("user") for row in rows}
    for user in mapped_users:
        if not user or user in existing or _is_demo_analytics_user(user):
            continue
        rows.append({"user": user, **_summary_from_bucket_rows([], unique_calls=0)})
        existing.add(user)


def _is_demo_analytics_user(user: str | None) -> bool:
    user = (user or "").strip().lower()
    return user.startswith("demo.agent.") or user == "offline.agent.demo@sriaas.com"


def _analytics_mapped_agent_queue_filter(queue_source: str | None = None) -> list[str]:
    if queue_source in {"CRM Lead", "Discontinued"}:
        return ["CRM Lead", "CRM Lead and Patient"]
    if queue_source == "Patient":
        return ["Patient", "CRM Lead and Patient"]
    return []


def _analytics_mapped_agent_scope(
    *,
    queue_source: str | None = None,
    agent_user: str | list[str] | None = None,
    lead_owner: str | list[str] | None = None,
    team_scope: list[str] | None = None,
) -> set[str] | None:
    agent_users = _analytics_filter_values(agent_user)
    if agent_users:
        return {user for user in agent_users if user}
    lead_owners = _analytics_filter_values(lead_owner)
    if queue_source in {"CRM Lead", "Discontinued"} and lead_owners:
        return {user for user in lead_owners if user}
    if team_scope:
        return {user for user in team_scope if user}
    return None


def _availability_attendance_rows(users: list[str], now) -> dict[str, list[Any]]:
    """Load today's availability transitions for all visible agents in one query."""
    if not users or not _agent_attendance_log_enabled():
        return {}
    meta = frappe.get_meta(AGENT_ATTENDANCE_DOCTYPE)
    if not meta.has_field("availability_status"):
        return {}

    rows = frappe.get_all(
        AGENT_ATTENDANCE_DOCTYPE,
        filters={
            "agent_user": ["in", list(dict.fromkeys(users))],
            "shift_date": _shift_date(now),
            "source": AVAILABILITY_ATTENDANCE_SOURCE,
        },
        fields=[
            "agent_user",
            "availability_status",
            "online_from",
            "last_seen_at",
            "offline_at",
            "status",
        ],
        order_by="agent_user asc, online_from asc",
        limit_page_length=10000,
    )
    by_user: dict[str, list[Any]] = {}
    for attendance_row in rows:
        by_user.setdefault(attendance_row.agent_user, []).append(attendance_row)
    return by_user


def _attach_agent_availability(rows: list[dict[str, Any]]) -> None:
    users = [row.get("user") for row in rows if row.get("user") and row.get("user") != _("Unassigned")]
    if not users or not frappe.db.exists("DocType", "Vobiz User Mapping"):
        return

    mappings = frappe.get_all(
        "Vobiz User Mapping",
        filters={"user": ["in", users], "enabled": 1},
        fields=["user", "availability_status", "accept_calls", "current_call_log", "last_status_at"],
        limit_page_length=2000,
    )
    by_user = {row.user: row for row in mappings}
    now = _now_ist()
    attendance_by_user = _availability_attendance_rows(users, now)
    shift_start, shift_end, shift_elapsed_until, shift_elapsed_seconds = _today_shift_window(now)
    shift_min_label = _human_duration_seconds(AGENT_SHIFT_MIN_SECONDS)
    for row in rows:
        mapping = by_user.get(row.get("user"))
        if not mapping:
            row.update(
                {
                    "availability_status": "Offline",
                    "is_on_call": False,
                    "current_call_log": "",
                    "is_online": False,
                    "availability_label": _("Offline"),
                    "availability_duration_label": "",
                    "online_today_seconds": 0,
                    "break_today_seconds": 0,
                    "offline_today_seconds": shift_elapsed_seconds,
                    "online_today_label": _human_duration_seconds(0),
                    "break_today_label": _human_duration_seconds(0),
                    "offline_today_label": _human_duration_seconds(shift_elapsed_seconds),
                    "current_availability_label": _("Offline"),
                    "current_availability_duration_label": _human_duration_seconds(shift_elapsed_seconds),
                    "current_availability_duration_seconds": shift_elapsed_seconds,
                    "break_count": 0,
                    "over_break_count": 0,
                    "has_over_break": False,
                    "break_limit_seconds": AGENT_BREAK_LIMIT_SECONDS,
                    "break_limit_label": _human_duration_seconds(AGENT_BREAK_LIMIT_SECONDS),
                    "attendance_records": [],
                    "shift_start": shift_start,
                    "shift_end": shift_end,
                    "shift_min_seconds": AGENT_SHIFT_MIN_SECONDS,
                    "shift_min_label": shift_min_label,
                }
            )
            continue

        active_call = is_active_call_log(mapping.current_call_log)
        heartbeat_online = is_agent_console_online(row.get("user"))
        stored_status = mapping.availability_status or "Available"
        status = "Busy" if active_call and stored_status == "Available" else stored_status
        is_online = status in {"Available", "Busy"}
        attendance = _availability_attendance_snapshot(
            row.get("user"),
            now,
            stored_status,
            heartbeat_online,
            mapping.last_status_at,
            rows=attendance_by_user.get(row.get("user"), []),
        )
        row.update(
            {
                "availability_status": status,
                "is_on_call": active_call,
                "current_call_log": mapping.current_call_log if active_call else "",
                "is_online": is_online,
                "availability_label": _availability_label(status),
                "availability_duration_label": attendance["current_duration_label"],
                "online_today_seconds": attendance["online_seconds"],
                "break_today_seconds": attendance["break_seconds"],
                "offline_today_seconds": attendance["offline_seconds"],
                "online_today_label": _human_duration_seconds(attendance["online_seconds"]),
                "break_today_label": _human_duration_seconds(attendance["break_seconds"]),
                "offline_today_label": _human_duration_seconds(attendance["offline_seconds"]),
                "current_availability_label": attendance["current_label"],
                "current_availability_duration_label": attendance["current_duration_label"],
                "current_availability_duration_seconds": attendance["current_duration_seconds"],
                "current_availability_since_epoch_ms": attendance["current_since_epoch_ms"],
                "break_count": attendance["break_count"],
                "over_break_count": attendance.get("over_break_count", 0),
                "has_over_break": bool(attendance.get("has_over_break")),
                "break_limit_seconds": AGENT_BREAK_LIMIT_SECONDS,
                "break_limit_label": _human_duration_seconds(AGENT_BREAK_LIMIT_SECONDS),
                "attendance_records": attendance["records"],
                "shift_start": shift_start,
                "shift_end": shift_end,
                "shift_min_seconds": AGENT_SHIFT_MIN_SECONDS,
                "shift_min_label": shift_min_label,
            }
        )


def _availability_label(status: str | None) -> str:
    status = status or "Offline"
    if status == "Away":
        return _("Break")
    if status == "Available":
        return _("Online")
    if status == "Busy":
        return _("Busy")
    return _("Offline")


def _availability_bucket(status: str | None) -> str:
    if status in {"Available", "Busy"}:
        return "online"
    if status == "Away":
        return "break"
    return "offline"


def _ist_epoch_ms(value) -> int:
    if not value:
        return 0
    dt = frappe.utils.get_datetime(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=AGENT_ATTENDANCE_TZ)
    return int(dt.timestamp() * 1000)


def _repair_stale_availability_end(row, row_end, next_row):
    start = frappe.utils.get_datetime(row.online_from)
    last_seen = frappe.utils.get_datetime(row.last_seen_at or row.online_from)
    row_end = frappe.utils.get_datetime(row_end)
    next_start = frappe.utils.get_datetime(next_row.online_from)
    stale_end = last_seen + timedelta(seconds=CONSOLE_SESSION_TTL_SECONDS)
    closed_after_ttl = abs((row_end - stale_end).total_seconds()) <= 2
    if closed_after_ttl and next_start > row_end:
        return next_start
    return row_end


def _availability_attendance_snapshot(
    user: str,
    now,
    current_status: str,
    heartbeat_online: bool,
    current_status_since=None,
    rows=None,
) -> dict[str, Any]:
    if not user or not _agent_attendance_log_enabled() or not frappe.get_meta(AGENT_ATTENDANCE_DOCTYPE).has_field("availability_status"):
        legacy = _attendance_snapshot(user, now, heartbeat_online, use_persistent=False)
        current_seconds = max(0, int((now - legacy["since"]).total_seconds())) if legacy.get("since") else 0
        return {
            "since": legacy.get("since"),
            "current_since_epoch_ms": _ist_epoch_ms(legacy.get("since")),
            "online_seconds": legacy.get("online_seconds", 0),
            "break_seconds": 0,
            "offline_seconds": legacy.get("offline_seconds", 0),
            "current_label": _("Online") if heartbeat_online else _("Offline"),
            "current_duration_label": _human_duration(now - legacy["since"]) if legacy.get("since") else "",
            "current_duration_seconds": current_seconds,
            "break_count": 0,
            "over_break_count": 0,
            "has_over_break": False,
            "records": [],
        }

<<<<<<< HEAD
    shift_start, _, shift_elapsed_until, shift_elapsed_seconds = _today_shift_window(now)
    if rows is None:
        rows = frappe.get_all(
            AGENT_ATTENDANCE_DOCTYPE,
            filters={"agent_user": user, "shift_date": _shift_date(now), "source": AVAILABILITY_ATTENDANCE_SOURCE},
            fields=["availability_status", "online_from", "last_seen_at", "offline_at", "status"],
            order_by="online_from asc",
            limit_page_length=500,
        )
=======
    shift_start, _shift_end, shift_elapsed_until, shift_elapsed_seconds = _today_shift_window(now)
    rows = frappe.get_all(
        AGENT_ATTENDANCE_DOCTYPE,
        filters={"agent_user": user, "shift_date": _shift_date(now), "source": AVAILABILITY_ATTENDANCE_SOURCE},
        fields=["availability_status", "online_from", "last_seen_at", "offline_at", "status"],
        order_by="online_from asc",
        limit_page_length=500,
    )
>>>>>>> b3cb8f5c397f60b62cf7831ce64a746203540c55
    authoritative_since = frappe.utils.get_datetime(current_status_since) if current_status_since else None
    if authoritative_since and authoritative_since > now:
        authoritative_since = None

    if not rows:
        if authoritative_since:
            shift_current_since = max(authoritative_since, shift_start)
            current_seconds = max(0, int((shift_elapsed_until - shift_current_since).total_seconds()))
            current_bucket = _availability_bucket(current_status)
            totals = {"online": 0, "break": 0, "offline": max(0, int((shift_current_since - shift_start).total_seconds()))}
            totals[current_bucket] += current_seconds
            records = []
            if current_status == "Away":
                over_break_seconds = max(0, current_seconds - AGENT_BREAK_LIMIT_SECONDS)
                records.append(
                    {
                        "status": current_status,
                        "label": _availability_label(current_status),
                        "bucket": "break",
                        "from": frappe.utils.format_datetime(shift_current_since),
                        "from_epoch_ms": _ist_epoch_ms(shift_current_since),
                        "to": "",
                        "to_epoch_ms": 0,
                        "duration_seconds": current_seconds,
                        "duration_label": _human_duration_seconds(current_seconds),
                        "is_over_break": current_seconds > AGENT_BREAK_LIMIT_SECONDS,
                        "over_break_seconds": over_break_seconds,
                        "over_break_label": _human_duration_seconds(over_break_seconds) if over_break_seconds else "",
                        "is_current": True,
                    }
                )
            return {
                "since": shift_current_since,
                "current_since_epoch_ms": _ist_epoch_ms(shift_current_since),
                "online_seconds": max(0, totals["online"]),
                "break_seconds": max(0, totals["break"]),
                "offline_seconds": max(0, totals["offline"]),
                "current_label": _availability_label(current_status),
                "current_duration_label": _human_duration_seconds(current_seconds),
                "current_duration_seconds": current_seconds,
                "break_count": 1 if current_status == "Away" else 0,
                "over_break_count": 1 if current_status == "Away" and current_seconds > AGENT_BREAK_LIMIT_SECONDS else 0,
                "has_over_break": current_status == "Away" and current_seconds > AGENT_BREAK_LIMIT_SECONDS,
                "records": records,
            }
        fallback = _attendance_snapshot(user, now, heartbeat_online, use_persistent=False)
        fallback_current = "Available" if heartbeat_online else "Offline"
        current_seconds = max(0, int((now - fallback["since"]).total_seconds())) if fallback.get("since") else 0
        return {
            "since": fallback.get("since"),
            "current_since_epoch_ms": _ist_epoch_ms(fallback.get("since")),
            "online_seconds": fallback.get("online_seconds", 0),
            "break_seconds": 0,
            "offline_seconds": fallback.get("offline_seconds", 0),
            "current_label": _availability_label(fallback_current),
            "current_duration_label": _human_duration(now - fallback["since"]) if fallback.get("since") else "",
            "current_duration_seconds": current_seconds,
            "break_count": 0,
            "over_break_count": 0,
            "has_over_break": False,
            "records": [],
        }

    totals = {"online": 0, "break": 0, "offline": 0}
    records = []
    cursor = shift_start
    current_since = None
    current_label = _availability_label(current_status)
    break_count = 0
    over_break_count = 0

    def add_record(status, start, end, is_current=False):
        nonlocal break_count, over_break_count
        start = max(frappe.utils.get_datetime(start), shift_start)
        end = min(frappe.utils.get_datetime(end), shift_elapsed_until)
        if end <= start:
            return
        bucket = _availability_bucket(status)
        seconds = max(0, int((end - start).total_seconds()))
        totals[bucket] += seconds
        is_break = status == "Away"
        over_break_seconds = max(0, seconds - AGENT_BREAK_LIMIT_SECONDS) if is_break else 0
        if status == "Away":
            break_count += 1
            if over_break_seconds:
                over_break_count += 1
        records.append(
            {
                "status": status,
                "label": _availability_label(status),
                "bucket": bucket,
                "from": frappe.utils.format_datetime(start),
                "from_epoch_ms": _ist_epoch_ms(start),
                "to": "" if is_current else frappe.utils.format_datetime(end),
                "to_epoch_ms": 0 if is_current else _ist_epoch_ms(end),
                "duration_seconds": seconds,
                "duration_label": _human_duration_seconds(seconds),
                "is_over_break": bool(over_break_seconds),
                "over_break_seconds": over_break_seconds,
                "over_break_label": _human_duration_seconds(over_break_seconds) if over_break_seconds else "",
                "is_current": is_current,
            }
        )

    if authoritative_since:
        authoritative_since = max(authoritative_since, shift_start)
        authoritative_since = min(authoritative_since, shift_elapsed_until)

    for index, row in enumerate(rows):
        start = max(frappe.utils.get_datetime(row.online_from), shift_start)
        if start > cursor:
            add_record("Offline", cursor, start)
        row_end = shift_elapsed_until if row.status == "Open" else frappe.utils.get_datetime(row.offline_at or row.last_seen_at or row.online_from)
        next_row = rows[index + 1] if index + 1 < len(rows) else None
        if row.status != "Open" and next_row:
            row_end = _repair_stale_availability_end(row, row_end, next_row)
        if authoritative_since and start >= authoritative_since:
            cursor = max(cursor, authoritative_since)
            continue
        if (
            authoritative_since
            and row_end > authoritative_since
            and (row.availability_status or "Offline") == (current_status or "Offline")
        ):
            cursor = max(cursor, authoritative_since)
            continue
        if authoritative_since and row_end > authoritative_since:
            row_end = authoritative_since
        if row.status == "Open":
            end = row_end
            is_current = not authoritative_since
            if is_current:
                current_since = start
                current_label = _availability_label(row.availability_status or current_status)
        else:
            end = row_end
            is_current = False
        add_record(row.availability_status or "Offline", start, end, is_current=is_current)
        cursor = max(cursor, min(end, shift_elapsed_until))

    if authoritative_since:
        if cursor < authoritative_since:
            add_record("Offline", cursor, authoritative_since)
        add_record(current_status or "Offline", authoritative_since, shift_elapsed_until, is_current=True)
        current_since = authoritative_since
        current_label = _availability_label(current_status)
        cursor = shift_elapsed_until

    if cursor < shift_elapsed_until:
        add_record("Offline", cursor, shift_elapsed_until, is_current=current_status == "Offline")
        if current_status == "Offline":
            current_since = cursor
            current_label = _("Offline")

    total_recorded = totals["online"] + totals["break"] + totals["offline"]
    if total_recorded < shift_elapsed_seconds:
        totals["offline"] += shift_elapsed_seconds - total_recorded

    current_duration_seconds = max(0, int((now - current_since).total_seconds())) if current_since else 0
    current_duration_label = _human_duration_seconds(current_duration_seconds) if current_since else ""
    return {
        "since": current_since,
        "current_since_epoch_ms": _ist_epoch_ms(current_since),
        "online_seconds": max(0, totals["online"]),
        "break_seconds": max(0, totals["break"]),
        "offline_seconds": max(0, totals["offline"]),
        "current_label": current_label,
        "current_duration_label": current_duration_label,
        "current_duration_seconds": current_duration_seconds,
        "break_count": break_count,
        "over_break_count": over_break_count,
        "has_over_break": over_break_count > 0,
        "records": records[-30:],
    }


def _now_ist():
    return datetime.now(AGENT_ATTENDANCE_TZ).replace(tzinfo=None)


def _clean_tab_id(tab_id: str | None) -> str:
    tab_id = re.sub(r"[^A-Za-z0-9_-]", "", str(tab_id or ""))[:80]
    return tab_id or "default"


def _should_touch_heartbeat_db(user: str, tab_id: str) -> bool:
    key = _console_heartbeat_db_touch_key(user, tab_id)
    try:
        if frappe.cache().get_value(key):
            return False
        frappe.cache().set_value(key, frappe.utils.now(), expires_in_sec=CONSOLE_HEARTBEAT_DB_TOUCH_SECONDS)
        return True
    except Exception:
        return True


def _should_touch_availability(user: str) -> bool:
    key = _console_availability_touch_key(user)
    try:
        if frappe.cache().get_value(key):
            return False
        frappe.cache().set_value(key, frappe.utils.now(), expires_in_sec=CONSOLE_AVAILABILITY_TOUCH_SECONDS)
        return True
    except Exception:
        return True


def _should_touch_desk_activity_db(user: str, tab_id: str) -> bool:
    key = _desk_activity_db_touch_key(user, tab_id)
    try:
        if frappe.cache().get_value(key):
            return False
        frappe.cache().set_value(key, frappe.utils.now(), expires_in_sec=DESK_ACTIVITY_DB_TOUCH_SECONDS)
        return True
    except Exception:
        # Redis is the liveness layer. Do not amplify a Redis incident into a
        # MariaDB write storm by falling back to a persistent touch.
        return False


def _remember_console_tab(user: str, tab_id: str) -> None:
    tabs = _console_tab_ids(user)
    if tab_id not in tabs:
        tabs.append(tab_id)
    frappe.cache().set_value(_console_tabs_key(user), tabs[-20:], expires_in_sec=24 * 60 * 60)


def _forget_console_tab(user: str, tab_id: str) -> None:
    tabs = [value for value in _console_tab_ids(user) if value != tab_id]
    frappe.cache().set_value(_console_tabs_key(user), tabs, expires_in_sec=24 * 60 * 60)


def _console_tab_ids(user: str) -> list[str]:
    try:
        tabs = frappe.cache().get_value(_console_tabs_key(user)) or []
    except Exception:
        tabs = []
    if not isinstance(tabs, list):
        return []
    return [str(tab_id) for tab_id in tabs if tab_id]


def _active_console_tabs(user: str) -> int:
    active = []
    for tab_id in _console_tab_ids(user):
        if frappe.cache().get_value(_console_tab_key(user, tab_id)):
            active.append(tab_id)
    if active != _console_tab_ids(user):
        frappe.cache().set_value(_console_tabs_key(user), active, expires_in_sec=24 * 60 * 60)
    return len(active)


def _today_shift_window(now) -> tuple[Any, Any, Any, int]:
    today_start = datetime.combine(now.date(), datetime.min.time())
    shift_start = today_start + timedelta(hours=AGENT_SHIFT_START_HOUR)
    shift_end = today_start + timedelta(hours=AGENT_SHIFT_END_HOUR)
    elapsed_until = min(max(now, shift_start), shift_end)
    elapsed_seconds = max(0, int((elapsed_until - shift_start).total_seconds()))
    return shift_start, shift_end, elapsed_until, elapsed_seconds


def _shift_date(now) -> str:
    return str(now.date())


def _default_attendance_state(now) -> dict[str, Any]:
    shift_start, _, _, _ = _today_shift_window(now)
    return {
        "date": _shift_date(now),
        "status": "offline",
        "since": str(shift_start),
        "online_seconds": 0,
        "offline_seconds": 0,
        "last_seen": "",
    }


def _get_attendance_state(user: str, now) -> dict[str, Any]:
    try:
        state = frappe.cache().get_value(_console_attendance_key(user, _shift_date(now))) or {}
    except Exception:
        state = {}
    if not isinstance(state, dict) or state.get("date") != _shift_date(now):
        return _default_attendance_state(now)
    return {**_default_attendance_state(now), **state}


def _save_attendance_state(user: str, state: dict[str, Any]) -> None:
    frappe.cache().set_value(_console_attendance_key(user, state["date"]), state, expires_in_sec=36 * 60 * 60)


def _set_attendance_state(user: str, online: bool, now=None) -> dict[str, Any]:
    now = now or _now_ist()
    state = _get_attendance_state(user, now)
    new_status = "online" if online else "offline"
    since = frappe.utils.get_datetime(state.get("since")) if state.get("since") else now
    if state.get("status") != new_status:
        seconds = _shift_overlap_seconds(since, now, now)
        if state.get("status") == "online":
            state["online_seconds"] = frappe.utils.cint(state.get("online_seconds")) + seconds
        else:
            state["offline_seconds"] = frappe.utils.cint(state.get("offline_seconds")) + seconds
        state["status"] = new_status
        state["since"] = str(now)
    if online:
        state["last_seen"] = str(now)
    _save_attendance_state(user, state)
    return state


def _attendance_snapshot(user: str, now, is_online: bool, *, use_persistent: bool = True) -> dict[str, Any]:
    if use_persistent and _agent_attendance_log_enabled():
        return _persistent_attendance_snapshot(user, now, is_online)

    state = _get_attendance_state(user, now)
    if state.get("status") == "online" and not is_online:
        last_seen = frappe.utils.get_datetime(state.get("last_seen")) if state.get("last_seen") else now
        offline_at = min(now, last_seen + timedelta(seconds=CONSOLE_SESSION_TTL_SECONDS))
        state = _set_attendance_state(user, False, offline_at)
    elif state.get("status") == "offline" and is_online:
        state = _set_attendance_state(user, True, now)

    since = frappe.utils.get_datetime(state.get("since")) if state.get("since") else now
    online_seconds = frappe.utils.cint(state.get("online_seconds"))
    offline_seconds = frappe.utils.cint(state.get("offline_seconds"))
    current_seconds = _shift_overlap_seconds(since, now, now)
    if state.get("status") == "online":
        online_seconds += current_seconds
    else:
        offline_seconds += current_seconds

    _, _, _, shift_elapsed_seconds = _today_shift_window(now)
    online_seconds = max(0, min(online_seconds, shift_elapsed_seconds))
    offline_seconds = max(0, min(offline_seconds, shift_elapsed_seconds - online_seconds))
    return {
        "since": since,
        "online_seconds": online_seconds,
        "offline_seconds": offline_seconds,
    }


def _agent_attendance_log_enabled() -> bool:
    return agent_attendance_enabled() and bool(
        frappe.db.exists("DocType", AGENT_ATTENDANCE_DOCTYPE)
    )


def _has_enabled_vobiz_user_mapping(user: str | None) -> bool:
    if not user or not frappe.db.exists("DocType", "Vobiz User Mapping"):
        return False
    return bool(frappe.db.get_value("Vobiz User Mapping", {"user": user, "enabled": 1}, "name"))


def _open_or_touch_attendance_session(
    user: str,
    tab_id: str,
    now=None,
    source: str = "Agent Console",
) -> None:
    if not user or not _agent_attendance_log_enabled():
        return
    now = now or _now_ist()
    shift_date = _shift_date(now)
    _close_stale_attendance_sessions(user, now)
    name = frappe.db.get_value(
        AGENT_ATTENDANCE_DOCTYPE,
        {"agent_user": user, "tab_id": tab_id, "shift_date": shift_date, "status": "Open"},
        "name",
        order_by="creation desc",
    )
    if name:
        frappe.db.set_value(
            AGENT_ATTENDANCE_DOCTYPE,
            name,
            {"last_seen_at": now},
            update_modified=False,
        )
        return
    frappe.get_doc(
        {
            "doctype": AGENT_ATTENDANCE_DOCTYPE,
            "agent_user": user,
            "tab_id": tab_id,
            "status": "Open",
            "shift_date": shift_date,
            "online_from": now,
            "last_seen_at": now,
            "duration_seconds": 0,
            "source": source,
        }
    ).insert(ignore_permissions=True)


def _close_attendance_session(user: str, tab_id: str, closed_at=None) -> None:
    if not user or not _agent_attendance_log_enabled():
        return
    closed_at = closed_at or _now_ist()
    rows = frappe.get_all(
        AGENT_ATTENDANCE_DOCTYPE,
        filters={"agent_user": user, "tab_id": tab_id, "status": "Open"},
        fields=["name", "online_from"],
        order_by="creation desc",
        limit_page_length=20,
    )
    for row in rows:
        _close_attendance_row(row.name, row.online_from, closed_at)


def _close_all_activity_sessions(user: str, closed_at=None) -> None:
    if not user or not _agent_attendance_log_enabled():
        return
    closed_at = closed_at or _now_ist()
    rows = frappe.get_all(
        AGENT_ATTENDANCE_DOCTYPE,
        filters={"agent_user": user, "status": "Open", "source": ["in", ACTIVITY_ATTENDANCE_SOURCES]},
        fields=["name", "online_from"],
        order_by="creation desc",
        limit_page_length=100,
    )
    for row in rows:
        _close_attendance_row(row.name, row.online_from, closed_at)


def _close_stale_attendance_sessions(user: str, now=None) -> None:
    if not user or not _agent_attendance_log_enabled():
        return
    now = now or _now_ist()
    rows = frappe.get_all(
        AGENT_ATTENDANCE_DOCTYPE,
        filters={
            "agent_user": user,
            "status": "Open",
            "shift_date": _shift_date(now),
            "source": ["in", ACTIVITY_ATTENDANCE_SOURCES],
        },
        fields=["name", "online_from", "last_seen_at", "source"],
        limit_page_length=100,
    )
    for row in rows:
        last_seen = frappe.utils.get_datetime(row.last_seen_at or row.online_from)
        stale_seconds = (
            DESK_ACTIVITY_STALE_SECONDS
            if row.source == "Desk Activity"
            else CONSOLE_SESSION_TTL_SECONDS
        )
        stale_at = last_seen + timedelta(seconds=stale_seconds)
        if stale_at < now:
            _close_attendance_row(row.name, row.online_from, stale_at)


def close_stale_agent_attendance_sessions() -> dict[str, int | bool]:
    if not _agent_attendance_log_enabled():
        return {"closed": 0, "disabled": True}
    now = _now_ist()
    rows = frappe.get_all(
        AGENT_ATTENDANCE_DOCTYPE,
        filters={"status": "Open", "source": ["in", ACTIVITY_ATTENDANCE_SOURCES]},
        fields=["name", "agent_user", "online_from", "last_seen_at", "source"],
        limit_page_length=1000,
    )
    closed = 0
    for row in rows:
        last_seen = frappe.utils.get_datetime(row.last_seen_at or row.online_from)
        stale_seconds = (
            DESK_ACTIVITY_STALE_SECONDS
            if row.source == "Desk Activity"
            else CONSOLE_SESSION_TTL_SECONDS
        )
        stale_at = last_seen + timedelta(seconds=stale_seconds)
        if stale_at < now:
            _close_attendance_row(row.name, row.online_from, stale_at)
            closed += 1
    if closed:
        frappe.db.commit()
    return {"closed": closed, "disabled": False}


def _close_attendance_row(name: str, online_from, offline_at) -> None:
    online_from = frappe.utils.get_datetime(online_from)
    offline_at = frappe.utils.get_datetime(offline_at)
    duration_seconds = max(0, int((offline_at - online_from).total_seconds()))
    frappe.db.set_value(
        AGENT_ATTENDANCE_DOCTYPE,
        name,
        {
            "status": "Closed",
            "offline_at": offline_at,
            "duration_seconds": duration_seconds,
        },
        update_modified=False,
    )


def _persistent_attendance_snapshot(user: str, now, is_online: bool) -> dict[str, Any]:
    _close_stale_attendance_sessions(user, now)
    shift_start, shift_end, shift_elapsed_until, shift_elapsed_seconds = _today_shift_window(now)
    rows = frappe.get_all(
        AGENT_ATTENDANCE_DOCTYPE,
        filters={"agent_user": user, "shift_date": _shift_date(now)},
        fields=["online_from", "last_seen_at", "offline_at", "status"],
        order_by="online_from asc",
        limit_page_length=500,
    )
    intervals = []
    active_since = None
    for row in rows:
        start = frappe.utils.get_datetime(row.online_from)
        if row.status == "Open":
            last_seen = frappe.utils.get_datetime(row.last_seen_at or row.online_from)
            end = now if is_online else min(now, last_seen + timedelta(seconds=CONSOLE_SESSION_TTL_SECONDS))
            active_since = active_since or start
        else:
            end = frappe.utils.get_datetime(row.offline_at or row.last_seen_at or row.online_from)
        start = max(start, shift_start)
        end = min(end, shift_elapsed_until)
        if end > start:
            intervals.append((start, end))

    online_seconds = _merged_period_seconds(intervals)
    online_seconds = max(0, min(online_seconds, shift_elapsed_seconds))
    offline_seconds = max(0, shift_elapsed_seconds - online_seconds)
    return {
        "since": active_since if is_online else now,
        "online_seconds": online_seconds,
        "offline_seconds": offline_seconds,
    }


def _merged_period_seconds(intervals: list[tuple[Any, Any]]) -> int:
    if not intervals:
        return 0
    ordered = sorted((frappe.utils.get_datetime(start), frappe.utils.get_datetime(end)) for start, end in intervals)
    merged = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
            continue
        merged[-1][1] = max(merged[-1][1], end)
    return sum(max(0, int((end - start).total_seconds())) for start, end in merged)


def _period_overlap_seconds(start, end, window_start, window_end) -> int:
    start = max(frappe.utils.get_datetime(start), frappe.utils.get_datetime(window_start))
    end = min(frappe.utils.get_datetime(end), frappe.utils.get_datetime(window_end))
    return max(0, int((end - start).total_seconds()))


def _shift_overlap_seconds(start, end, now) -> int:
    shift_start, shift_end, shift_elapsed_until, _ = _today_shift_window(now)
    start = max(frappe.utils.get_datetime(start), shift_start)
    end = min(frappe.utils.get_datetime(end), shift_end, shift_elapsed_until)
    return max(0, int((end - start).total_seconds()))


def _human_duration_seconds(seconds: int) -> str:
    if seconds <= 0:
        return _("0m")
    return _human_duration(timedelta(seconds=seconds))


def _human_duration(delta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return _("{0}d {1}h").format(days, hours)
    if hours:
        return _("{0}h {1}m").format(hours, minutes)
    if minutes:
        return _("{0}m").format(minutes)
    return _("just now")


def _analytics_call_rows_sql(
    conditions: str,
    params: dict[str, Any],
    fields: list[str],
    *,
    status_filter: str | None,
    limit: int,
    offset: int,
    queue_source: str | None = None,
    unique_only: bool = False,
) -> list[dict[str, Any]]:
    bucket_expr = _analytics_bucket_sql()
    bucket_filter = _analytics_bucket_filter_sql(status_filter)
    unique_key_expr = _analytics_unique_key_sql()
    select_fields = ", ".join(f"`{field}`" for field in fields)
    query_params = {**params, "limit": int(limit), "offset": int(offset)}
    if queue_source in {"CRM Lead", "Discontinued"} and frappe.db.exists("DocType", "CRM Lead"):
        if unique_only:
            joined_bucket_filter = _analytics_bucket_filter_sql(status_filter, "filtered")
            return frappe.db.sql(
                f"""
                select filtered.*, attempts.attempt_count, lead.`lead_owner` as analytics_agent, lead.`team` as analytics_team
                from (
                    select {select_fields}, {bucket_expr} as bucket, {_analytics_talk_sql()} as talk_seconds, {unique_key_expr} as unique_key
                    from `tabVobiz Call Log`
                    where {conditions}
                ) filtered
                inner join (
                    select unique_key, max(`creation`) as latest_creation, count(*) as attempt_count
                    from (
                        select `creation`, {bucket_expr} as bucket, `direction`, {unique_key_expr} as unique_key
                        from `tabVobiz Call Log`
                        where {conditions}
                    ) analytics
                    {bucket_filter}
                    group by unique_key
                ) attempts on attempts.unique_key = filtered.unique_key and attempts.latest_creation = filtered.`creation`
                left join `tabCRM Lead` lead on lead.`name` = filtered.`reference_name`
                {joined_bucket_filter}
                order by filtered.`creation` desc
                limit %(limit)s offset %(offset)s
                """,
                query_params,
                as_dict=True,
            )
        return frappe.db.sql(
            f"""
            select analytics.*, lead.`lead_owner` as analytics_agent, lead.`team` as analytics_team
            from (
                select {select_fields}, {bucket_expr} as bucket, {_analytics_talk_sql()} as talk_seconds
                from `tabVobiz Call Log`
                where {conditions}
            ) analytics
            left join `tabCRM Lead` lead on lead.`name` = analytics.`reference_name`
            {_analytics_bucket_filter_sql(status_filter, "analytics")}
            order by analytics.`creation` desc
            limit %(limit)s offset %(offset)s
            """,
            query_params,
            as_dict=True,
        )
    if unique_only:
        return frappe.db.sql(
            f"""
            select filtered.*, attempts.attempt_count
            from (
                select {select_fields}, {bucket_expr} as bucket, {_analytics_talk_sql()} as talk_seconds, {unique_key_expr} as unique_key
                from `tabVobiz Call Log`
                where {conditions}
            ) filtered
            inner join (
                select unique_key, max(`creation`) as latest_creation, count(*) as attempt_count
                from (
                    select `creation`, {bucket_expr} as bucket, `direction`, {unique_key_expr} as unique_key
                    from `tabVobiz Call Log`
                    where {conditions}
                ) analytics
                {bucket_filter}
                group by unique_key
            ) attempts on attempts.unique_key = filtered.unique_key and attempts.latest_creation = filtered.`creation`
            {_analytics_bucket_filter_sql(status_filter, "filtered")}
            order by filtered.`creation` desc
            limit %(limit)s offset %(offset)s
            """,
            query_params,
            as_dict=True,
        )
    return frappe.db.sql(
        f"""
        select *
        from (
            select {select_fields}, {bucket_expr} as bucket, {_analytics_talk_sql()} as talk_seconds
            from `tabVobiz Call Log`
            where {conditions}
        ) analytics
        {_analytics_bucket_filter_sql(status_filter, "analytics")}
        order by `creation` desc
        limit %(limit)s offset %(offset)s
        """,
        query_params,
        as_dict=True,
    )


def _performance_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    connected = [row for row in rows if row.get("bucket") == "connected"]
    missed = [
        row for row in rows
        if row.get("bucket") in {"missed", "busy", "no_answer", "failed", "cancelled"}
        and row.get("direction") == "Incoming"
    ]
    connected_seconds = [frappe.utils.cint(row.get("talk_seconds")) for row in connected if frappe.utils.cint(row.get("talk_seconds"))]
    total_talk_seconds = sum(connected_seconds)
    average_duration = round(total_talk_seconds / len(connected)) if connected else 0
    total = len(rows)
    return {
        "total": total,
        "connected": len(connected),
        "missed": len(missed),
        "busy": len([row for row in rows if row.get("bucket") == "busy"]),
        "no_answer": len([row for row in rows if row.get("bucket") == "no_answer"]),
        "failed": len([row for row in rows if row.get("bucket") == "failed"]),
        "cancelled": len([row for row in rows if row.get("bucket") == "cancelled"]),
        "talk_seconds": total_talk_seconds,
        "talk_time_label": _duration_label(total_talk_seconds),
        "average_duration": average_duration,
        "average_duration_label": _duration_label(average_duration),
        "answer_rate": round((len(connected) / total) * 100, 1) if total else 0,
        "missed_rate": round((len(missed) / total) * 100, 1) if total else 0,
        "cost": round(sum(frappe.utils.flt(row.get("cost")) for row in rows), 2),
    }


def _filter_performance_rows(rows: list[dict[str, Any]], status_filter: str | None) -> list[dict[str, Any]]:
    status_filter = _analytics_status_filter(status_filter)
    missed_buckets = {"missed", "busy", "no_answer", "failed", "cancelled"}
    if status_filter == "connected":
        return [row for row in rows if row.get("bucket") == "connected"]
    if status_filter == "missed":
        return [row for row in rows if row.get("bucket") in missed_buckets and row.get("direction") == "Incoming"]
    if status_filter in {"busy", "no_answer", "failed", "cancelled"}:
        return [row for row in rows if row.get("bucket") == status_filter]
    return rows


def _performance_by_user(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("user") or _("Unassigned"), []).append(row)
    data = []
    for user, user_rows in grouped.items():
        data.append({"user": user, **_performance_summary(user_rows)})
    return sorted(data, key=lambda row: row["total"], reverse=True)


def _performance_call_row(row) -> dict[str, Any]:
    return {
        "name": row.get("name"),
        "user": row.get("analytics_agent") if "analytics_agent" in row else row.get("user"),
        "call_user": row.get("user"),
        "team": row.get("analytics_team") if "analytics_team" in row else "",
        "status": row.get("status"),
        "bucket": row.get("bucket"),
        "bucket_label": row.get("bucket_label"),
        "talk_seconds": frappe.utils.cint(row.get("talk_seconds")),
        "duration_label": _duration_label(row.get("talk_seconds")),
        "creation": row.get("creation"),
        "reference_doctype": row.get("reference_doctype"),
        "reference_name": row.get("reference_name"),
        "customer_number": row.get("customer_number"),
        "attempt_count": frappe.utils.cint(row.get("attempt_count")) if row.get("attempt_count") is not None else None,
        "user_mobile": row.get("user_mobile"),
        "caller_id": row.get("caller_id"),
        "disposition": row.get("disposition"),
        "recording_status": row.get("recording_status"),
        "recording_download_url": recording_proxy_url(row.get("name")) if row.get("bucket") == "connected" and row.get("recording_url") else "",
    }


def _analytics_agent_options(
    is_admin: bool,
    team_scope: list[str] | None = None,
    queue_source: str | None = None,
    team: str | None = None,
    visible_leads: list[str] | None = None,
) -> list[str]:
    if team_scope:
        return team_scope
    if not is_admin:
        return [frappe.session.user]

    values: list[str] = []
    if frappe.db.exists("DocType", "Vobiz User Mapping"):
        try:
            values.extend(
                frappe.get_all(
                    "Vobiz User Mapping",
                    filters={"enabled": 1},
                    pluck="user",
                    order_by="user asc",
                )
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Vobiz analytics agent options failed")

    values.extend(
        frappe.get_all(
            "Vobiz Call Log",
            filters={"user": ["is", "set"]},
            pluck="user",
            distinct=True,
            order_by="user asc",
            limit_page_length=500,
        )
    )

    cleaned = []
    seen = set()
    for value in values:
        value = (value or "").strip()
        if value and value not in seen and not _is_demo_analytics_user(value):
            cleaned.append(value)
            seen.add(value)
    return cleaned


def _analytics_lead_owner_options(queue_source: str | None = None, team: str | list[str] | None = None, visible_leads: list[str] | None = None) -> list[str]:
    if queue_source not in {"CRM Lead", "Discontinued"}:
        return []
    return _crm_lead_distinct_options("lead_owner", team=team, visible_leads=visible_leads)


def _analytics_team_options(queue_source: str | None = None, visible_leads: list[str] | None = None) -> list[str]:
    if queue_source not in {"CRM Lead", "Discontinued"}:
        return []
    return _crm_lead_distinct_options("team", visible_leads=visible_leads)


def _analytics_department_options(queue_source: str | None = None) -> list[str]:
    if queue_source != "Patient":
        return []
    if not frappe.db.exists("DocType", "Patient"):
        return []
    meta = frappe.get_meta("Patient")
    if not meta.has_field("sr_medical_department"):
        return []
    rows = frappe.get_all(
        "Patient",
        filters={"sr_medical_department": ["is", "set"]},
        pluck="sr_medical_department",
        distinct=True,
        order_by="sr_medical_department asc",
        limit_page_length=500,
    )
    cleaned = []
    seen = set()
    for value in rows:
        value = (value or "").strip()
        if value and value not in seen:
            cleaned.append(value)
            seen.add(value)
    return cleaned


def _crm_lead_distinct_options(
    fieldname: str,
    *,
    team: str | list[str] | None = None,
    visible_leads: list[str] | None = None,
) -> list[str]:
    if not frappe.db.exists("DocType", "CRM Lead"):
        return []
    meta = frappe.get_meta("CRM Lead")
    if not meta.has_field(fieldname):
        return []
    filters: dict[str, Any] = {fieldname: ["is", "set"]}
    teams = _analytics_filter_values(team)
    if teams and fieldname != "team" and meta.has_field("team"):
        filters["team"] = ["in", teams] if len(teams) > 1 else teams[0]
    if visible_leads is not None:
        if not visible_leads:
            return []
        filters["name"] = ["in", visible_leads]
    rows = frappe.get_all(
        "CRM Lead",
        filters=filters,
        pluck=fieldname,
        distinct=True,
        order_by=f"{fieldname} asc",
        limit_page_length=500,
    )
    cleaned = []
    seen = set()
    for value in rows:
        value = (value or "").strip()
        if value and value not in seen and not (fieldname == "lead_owner" and _is_demo_analytics_user(value)):
            cleaned.append(value)
            seen.add(value)
    return cleaned


def _apply_visible_crm_lead_scope(filters: dict[str, Any]) -> None:
    if not frappe.db.exists("DocType", "CRM Lead"):
        if filters.get("reference_doctype") == "CRM Lead":
            filters["force_empty"] = True
        return

    lead_names = _visible_crm_lead_names()
    if filters.get("reference_doctype") == "CRM Lead":
        if lead_names:
            filters["reference_names"] = lead_names
        else:
            filters["force_empty"] = True
        return

    if lead_names:
        filters["crm_lead_reference_names"] = lead_names
    else:
        filters["exclude_crm_leads"] = True


def _visible_crm_lead_names() -> list[str]:
    try:
        return frappe.get_list(
            "CRM Lead",
            pluck="name",
            order_by="modified desc",
            limit_page_length=50000,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Vobiz analytics CRM Lead permission scope failed")
        return []


def _can_view_all_analytics_agents() -> bool:
    if frappe.session.user == "Administrator":
        return True
    roles = set(frappe.get_roles())
    manager_roles = {
        "System Manager",
        "Manager",
        "Vobiz Manager",
        "Call Center Manager",
        "Sales Manager",
        "Team Manager",
    }
    return bool(roles.intersection(manager_roles))


def _team_member_users_for_leader(team_leader: str | None) -> list[str]:
    team_leader = (team_leader or "").strip()
    if not team_leader:
        return []

    users = []
    seen = {team_leader}
    if frappe.db.exists("DocType", "Team") and frappe.db.exists("DocType", "Team User"):
        teams = frappe.get_all("Team", filters={"team_lead": team_leader, "is_active": 1}, pluck="name")
        if teams:
            users.extend(
                frappe.get_all(
                    "Team User",
                    filters={"parent": ["in", teams], "is_active": 1},
                    pluck="user",
                    order_by="user asc",
                )
            )

    if frappe.db.exists("DocType", "Vobiz User Mapping"):
        users.extend(
            frappe.get_all(
                "Vobiz User Mapping",
                filters={"enabled": 1, "team_leader": team_leader},
                pluck="user",
                order_by="user asc",
            )
        )

    cleaned = [team_leader]
    for user in users:
        user = (user or "").strip()
        if user and user not in seen:
            cleaned.append(user)
            seen.add(user)
    return cleaned if len(cleaned) > 1 else []


def _analytics_date_range(from_date: str | None, to_date: str | None) -> tuple[str, str]:
    today = frappe.utils.today()
    try:
        start = frappe.utils.getdate(from_date or today)
    except Exception:
        start = frappe.utils.getdate(today)
    try:
        end = frappe.utils.getdate(to_date or today)
    except Exception:
        end = frappe.utils.getdate(today)
    if start > end:
        start, end = end, start
    return str(start), str(end)


def _analytics_status_filter(status_filter: str | None) -> str:
    status_filter = (status_filter or "total").strip().lower().replace("-", "_")
    return status_filter if status_filter in ANALYTICS_STATUS_OPTIONS else "total"


def _analytics_row(row) -> dict[str, Any]:
    data = row.as_dict() if callable(getattr(row, "as_dict", None)) else dict(row)
    bucket = _analytics_bucket(data)
    data["bucket"] = bucket
    data["bucket_label"] = _analytics_bucket_label(bucket)
    data["talk_seconds"] = _talk_seconds(data)
    data["cost"] = frappe.utils.flt(data.get("cost"))
    return data


def _analytics_bucket(row: dict[str, Any]) -> str:
    return status_bucket(row)


def _analytics_bucket_label(bucket: str) -> str:
    return {
        "connected": _("Connected"),
        "missed": _("Missed"),
        "busy": _("Busy"),
        "no_answer": _("No Answer"),
        "failed": _("Failed"),
        "cancelled": _("Cancelled"),
        "other": _("Other"),
    }.get(bucket, _("Other"))


def _performance_status_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = _performance_summary(rows)
    data = [
        {"bucket": "connected", "label": _("Connected"), "count": summary.get("connected") or 0},
        {"bucket": "missed", "label": _("Missed"), "count": summary.get("missed") or 0},
    ]
    other = len([row for row in rows if row.get("bucket") == "other"])
    if other:
        data.append({"bucket": "other", "label": _("Other"), "count": other})
    return [row for row in data if row["count"]]


def _performance_outcome_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = ["connected", "busy", "no_answer", "failed", "cancelled", "missed", "other"]
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in order}
    for row in rows:
        grouped.setdefault(row.get("bucket") or "other", []).append(row)
    return [
        {
            "bucket": bucket,
            "label": _analytics_bucket_label(bucket),
            "count": len(grouped.get(bucket, [])),
            "average_duration_label": _performance_summary(grouped.get(bucket, [])).get("average_duration_label"),
        }
        for bucket in order
        if grouped.get(bucket)
    ]


def _performance_by_day(rows: list[dict[str, Any]], from_date: str, to_date: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        day = str(frappe.utils.getdate(row.get("creation"))) if row.get("creation") else ""
        grouped.setdefault(day, []).append(row)

    start = frappe.utils.getdate(from_date)
    end = frappe.utils.getdate(to_date)
    days = []
    current = start
    while current <= end:
        day = str(current)
        days.append({"date": day, **_performance_summary(grouped.get(day, []))})
        current = frappe.utils.add_days(current, 1)
    return days


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
    if not is_active_call_log(doc.name):
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

    meta = frappe.get_meta("Vobiz User Mapping")
    fields = ["name", "agent_mobile", "caller_id", "availability_status"]
    if meta.has_field("queue_source") and frappe.db.has_column("Vobiz User Mapping", "queue_source"):
        fields.append("queue_source")
    for fieldname in (
        "sr_medical_department",
        "sr_medical_departments",
        "sr_followup_id",
        "sr_followup_ids",
        "fallback_user",
        "fallback_users",
        "team",
        "team_leader",
    ):
        if meta.has_field(fieldname) and frappe.db.has_column("Vobiz User Mapping", fieldname):
            fields.append(fieldname)

    return frappe.db.get_value(
        "Vobiz User Mapping",
        {"user": frappe.session.user, "enabled": 1},
        fields,
        as_dict=True,
    ) or {}


def _agent_queue_source(agent: dict[str, Any] | None = None) -> str:
    source = ((agent or {}).get("queue_source") or "CRM Lead").strip()
    return source if source in QUEUE_SOURCE_DOCTYPES else "CRM Lead"


def _selected_queue_source(agent_queue_source: str, requested_source: str | None = None) -> str:
    requested_source = (requested_source or "").strip()
    if agent_queue_source == COMBINED_QUEUE_SOURCE:
        return requested_source if requested_source in {"CRM Lead", "Patient"} else "CRM Lead"
    return agent_queue_source


def _queue_source_options(agent_queue_source: str) -> list[str]:
    if agent_queue_source == COMBINED_QUEUE_SOURCE:
        return ["CRM Lead", "Patient"]
    return [agent_queue_source]


def _queue_doctype_for_source(queue_source: str) -> str:
    return QUEUE_SOURCE_DOCTYPES.get(queue_source, "CRM Lead")


def _queue_meta(queue_source: str, doctype: str, agent_queue_source: str | None = None) -> dict[str, Any]:
    agent_queue_source = agent_queue_source or queue_source
    source_options = _queue_source_options(agent_queue_source)
    if doctype == "Patient":
        return {
            "source": queue_source,
            "agent_source": agent_queue_source,
            "source_options": source_options,
            "doctype": doctype,
            "title": _("Patient Queue"),
            "id_label": _("Patient ID"),
            "selected_label": _("patients"),
            "summary_tab_label": _("Patient"),
            "data_label": _("Patient Data"),
            "empty_message": _("No callable patients found"),
            "followup_day_options": "\n".join(_patient_followup_day_options()),
        }
    if queue_source == "Discontinued":
        return {
            "source": queue_source,
            "agent_source": agent_queue_source,
            "source_options": source_options,
            "doctype": doctype,
            "title": _("Discontinued / Missed Call Queue"),
            "id_label": _("CRM Lead ID"),
            "selected_label": _("leads"),
            "summary_tab_label": _("CRM Lead"),
            "data_label": _("CRM Lead Data"),
            "empty_message": _("No missed or discontinued CRM leads found"),
        }
    if doctype == "Patient Encounter":
        return {
            "source": queue_source,
            "agent_source": agent_queue_source,
            "source_options": source_options,
            "doctype": doctype,
            "title": _("Patient Encounter Queue"),
            "id_label": _("Encounter ID"),
            "selected_label": _("encounters"),
            "summary_tab_label": _("Patient Encounter"),
            "data_label": _("Patient Encounter Data"),
            "empty_message": _("No patient encounters found"),
        }
    if doctype == "Issue":
        return {
            "source": queue_source,
            "agent_source": agent_queue_source,
            "source_options": source_options,
            "doctype": doctype,
            "title": _("Issue Queue"),
            "id_label": _("Issue ID"),
            "selected_label": _("issues"),
            "summary_tab_label": _("Issue"),
            "data_label": _("Issue Data"),
            "empty_message": _("No issues found"),
        }
    return {
        "source": queue_source,
        "agent_source": agent_queue_source,
        "source_options": source_options,
        "doctype": doctype,
        "title": _("Lead Queue"),
        "id_label": _("CRM Lead ID"),
        "selected_label": _("leads"),
        "summary_tab_label": _("CRM Lead"),
        "data_label": _("CRM Lead Data"),
        "empty_message": _("No callable records found"),
    }


def _lead_queue(
    limit: int,
    search: str | None = None,
    agent: dict[str, Any] | None = None,
    queue_source: str | None = None,
    followup_day: str | None = None,
    sort_by: str | None = None,
    user_filters: str | list | None = None,
) -> list[dict[str, Any]]:
    queue_source = queue_source or _agent_queue_source(agent)
    preferred_doctype = _queue_doctype_for_source(queue_source)
    if preferred_doctype and frappe.db.exists("DocType", preferred_doctype):
        return _queue_for_doctype(
            preferred_doctype,
            limit,
            search,
            queue_source=queue_source,
            agent=agent,
            followup_day=followup_day,
            sort_by=sort_by,
            user_filters=user_filters,
        )

    for doctype in LEAD_DOCTYPE_CANDIDATES:
        if frappe.db.exists("DocType", doctype):
            rows = _queue_for_doctype(
                doctype,
                limit,
                search,
                queue_source=queue_source,
                agent=agent,
                followup_day=followup_day,
                sort_by=sort_by,
                user_filters=user_filters,
            )
            if doctype == "CRM Lead":
                return rows
            if rows:
                return rows
    return []


def _queue_for_doctype(
    doctype: str,
    limit: int,
    search: str | None = None,
    queue_source: str | None = None,
    agent: dict[str, Any] | None = None,
    followup_day: str | None = None,
    sort_by: str | None = None,
    user_filters: str | list | None = None,
) -> list[dict[str, Any]]:
    meta = frappe.get_meta(doctype)
    fields = ["name", "creation", "modified"]
    for fieldname in _existing_fields(
        meta,
        (
            "lead_name",
            "first_name",
            "patient_name",
            "customer_name",
            "company_name",
            "subject",
            "customer",
            "patient",
            "practitioner",
            "encounter_date",
            "raised_by",
            "organization",
            "sr_pe_mobile",
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
            "created_by_agent",
            "sr_medical_department",
            "sr_followup_id",
            "sr_followup_day",
            "sr_followup_status",
            "team",
            "owner",
        ),
    ):
        if fieldname not in fields:
            fields.append(fieldname)

    filters = _queue_filter_list(meta, queue_source=queue_source, agent=agent, followup_day=followup_day)
    filters.extend(_safe_user_filters(meta, user_filters))
    query = (search or "").strip()
    search_fields = _queue_search_fields(meta, fields) if query else []
    fetch_rows = frappe.get_list if doctype == "CRM Lead" else frappe.get_all
    fetch_limit = max(limit, min(500, CONSOLE_QUEUE_LIMIT_MAX * 5))
    rows = fetch_rows(
        doctype,
        filters=filters,
        or_filters=_queue_search_filters(search_fields, query) if query else None,
        fields=fields,
        order_by=_queue_order_by(meta, sort_by),
        limit_page_length=fetch_limit,
    )
    rows = [_reference_row(doctype, row) for row in rows]
    rows = _attach_queue_missed_calls(rows)
    rows = _attach_queue_whatsapp(rows, sort_by=sort_by)
    return rows[:limit]


def _queue_order_by(meta, sort_by: str | None = None) -> str:
    sort_key = (sort_by or "").strip()
    standard_fields = {"name", "creation", "modified", "owner"}
    options = {
        "modified_desc": ("modified", "desc"),
        "modified_asc": ("modified", "asc"),
        "creation_desc": ("creation", "desc"),
        "creation_asc": ("creation", "asc"),
        "name_asc": ("name", "asc"),
        "name_desc": ("name", "desc"),
        "next_follow_up_asc": ("vobiz_next_follow_up", "asc"),
        "whatsapp_unread_desc": ("modified", "desc"),
    }
    fieldname, direction = options.get(sort_key, options["creation_desc"])
    if fieldname not in standard_fields and not meta.has_field(fieldname):
        fieldname, direction = options["creation_desc"]
    return f"{fieldname} {direction}"


def _queue_filters(meta, queue_source: str | None = None, agent: dict[str, Any] | None = None, followup_day: str | None = None) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if meta.name == "CRM Lead":
        if queue_source == "Discontinued" and meta.has_field("vobiz_last_call_status"):
            filters["vobiz_last_call_status"] = ["in", sorted(MISSED_STATUSES | {"Cancelled", "Canceled"})]
        return filters

    if meta.has_field("disabled"):
        filters["disabled"] = ["!=", 1]
    if meta.has_field("vobiz_do_not_call"):
        filters["vobiz_do_not_call"] = ["!=", 1]
    if meta.name == "Patient" and queue_source == "Patient":
        if meta.has_field("sr_medical_department"):
            departments = _split_values((agent or {}).get("sr_medical_departments"), first=(agent or {}).get("sr_medical_department"))
            filters["sr_medical_department"] = ["in", departments] if departments else "__no_patient_department_mapping__"
        if meta.has_field("sr_followup_id"):
            followup_ids = _split_values((agent or {}).get("sr_followup_ids"), first=(agent or {}).get("sr_followup_id"))
            filters["sr_followup_id"] = ["in", followup_ids] if followup_ids else "__no_patient_followup_mapping__"
        if followup_day and meta.has_field("sr_followup_day"):
            filters["sr_followup_day"] = followup_day
    return filters


def _queue_filter_list(meta, queue_source: str | None = None, agent: dict[str, Any] | None = None, followup_day: str | None = None) -> list[list[Any]]:
    rows = []
    for fieldname, value in _queue_filters(meta, queue_source=queue_source, agent=agent, followup_day=followup_day).items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            rows.append([meta.name, fieldname, value[0], value[1]])
        else:
            rows.append([meta.name, fieldname, "=", value])
    return rows


def _safe_user_filters(meta, raw_filters: str | list | None) -> list[list[Any]]:
    filters = _parse_user_filters(raw_filters)
    standard_fields = {
        "name",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "docstatus",
        "idx",
    }
    allowed_ops = {
        "=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in",
        "is", "is not", "between", "timespan", "previous", "next",
    }
    safe = []
    for item in filters:
        if not isinstance(item, (list, tuple)):
            continue
        if len(item) >= 4:
            doctype, fieldname, operator, value = item[:4]
        elif len(item) == 3:
            doctype, fieldname, operator, value = meta.name, item[0], item[1], item[2]
        else:
            continue
        if doctype and doctype != meta.name:
            continue
        fieldname = str(fieldname or "")
        operator = str(operator or "=").lower()
        if fieldname not in standard_fields and not meta.has_field(fieldname):
            continue
        if operator not in allowed_ops:
            continue
        safe.append([meta.name, fieldname, operator, value])
    return safe


def _parse_user_filters(raw_filters: str | list | None) -> list:
    if not raw_filters:
        return []
    if isinstance(raw_filters, str):
        try:
            parsed = json.loads(raw_filters)
        except Exception:
            return []
    else:
        parsed = raw_filters
    return parsed if isinstance(parsed, list) else []


def _patient_followup_day_options() -> list[str]:
    if not frappe.db.exists("DocType", "Patient"):
        return []
    meta = frappe.get_meta("Patient")
    field = meta.get_field("sr_followup_day")
    if not field:
        return []
    return [row.strip() for row in (field.options or "").splitlines() if row.strip()]


def _queue_search_fields(meta, loaded_fields: list[str]) -> list[str]:
    candidates = (
        "name",
        "lead_name",
        "first_name",
        "patient_name",
        "customer_name",
        "company_name",
        "subject",
        "customer",
        "patient",
        "practitioner",
        "encounter_date",
        "raised_by",
        "organization",
        "sr_pe_mobile",
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


def _split_values(value: str | None, first: str | None = None) -> list[str]:
    values = []
    seen = set()
    for raw in [first or "", value or ""]:
        for row in str(raw).replace(",", "\n").splitlines():
            row = row.strip()
            if row and row not in seen:
                values.append(row)
                seen.add(row)
    return values


def _reference_row(doctype: str, doc) -> dict[str, Any]:
    data = doc.as_dict() if callable(getattr(doc, "as_dict", None)) else dict(doc)
    title = (
        data.get("lead_name")
        or data.get("patient_name")
        or data.get("customer_name")
        or data.get("first_name")
        or data.get("company_name")
        or data.get("subject")
        or data.get("patient")
        or data.get("name")
    )
    company = data.get("company_name") or data.get("organization") or data.get("practitioner") or data.get("raised_by") or doctype
    phone_field = _first_value(data, _phone_field_candidates())
    if not phone_field.get("value"):
        phone_field = _linked_customer_phone(data.get("customer"))
    if doctype == "Patient":
        status = data.get("sr_followup_status") or _first_value(data, ("status",)).get("value") or "New"
    else:
        status = _first_value(data, ("status", "lead_status", "sr_lead_status", "qualification_status")).get("value") or "New"
    next_action = data.get("vobiz_last_call_status") or data.get("vobiz_next_follow_up") or data.get("encounter_date") or "Initial contact"

    return {
        "doctype": doctype,
        "name": data.get("name"),
        "creation": data.get("creation"),
        "modified": data.get("modified"),
        "title": title,
        "company": company,
        "phone": phone_field.get("value"),
        "phone_field": phone_field.get("fieldname"),
        "status": status,
        "next_action": str(next_action),
        "sr_medical_department": data.get("sr_medical_department"),
        "sr_followup_status": data.get("sr_followup_status"),
        "sr_followup_id": data.get("sr_followup_id"),
        "sr_followup_day": data.get("sr_followup_day"),
        "team": data.get("team"),
        "owner": data.get("lead_owner"),
    }


def _attach_queue_whatsapp(rows: list[dict[str, Any]], sort_by: str | None = None) -> list[dict[str, Any]]:
    if not rows:
        return _sort_queue_by_whatsapp(rows, sort_by)

    for row in rows:
        row.update({
            "whatsapp_conversation": None,
            "whatsapp_unread_count": 0,
            "whatsapp_last_message_preview": "",
            "whatsapp_last_message_at": None,
        })

    if not frappe.db.exists("DocType", "Chat Conversation"):
        return _sort_queue_by_whatsapp(rows, sort_by)

    conversation_meta = frappe.get_meta("Chat Conversation")
    fields = _existing_fields(
        conversation_meta,
        ("name", "unread_count", "last_message_preview", "modified"),
    )
    if "name" not in fields:
        fields.insert(0, "name")
    if "contact" not in fields and conversation_meta.has_field("contact"):
        fields.append("contact")

    pending = {(row.get("doctype"), row.get("name")) for row in rows if row.get("doctype") and row.get("name")}
    by_key = {(row.get("doctype"), row.get("name")): row for row in rows if row.get("doctype") and row.get("name")}

    def apply(row: dict[str, Any], data: dict[str, Any]) -> None:
        row.update({
            "whatsapp_conversation": data.get("name"),
            "whatsapp_unread_count": frappe.utils.cint(data.get("unread_count")),
            "whatsapp_last_message_preview": data.get("last_message_preview") or "",
            "whatsapp_last_message_at": data.get("modified"),
        })

    if conversation_meta.has_field("linked_reference_doctype") and conversation_meta.has_field("linked_reference_name"):
        doctypes = sorted({doctype for doctype, _name in pending})
        names = sorted({name for _doctype, name in pending})
        if doctypes and names:
            ref_fields = fields + ["linked_reference_doctype", "linked_reference_name"]
            for data in frappe.get_all(
                "Chat Conversation",
                filters=[
                    ["linked_reference_doctype", "in", doctypes],
                    ["linked_reference_name", "in", names],
                ],
                fields=ref_fields,
                order_by="modified desc",
                limit_page_length=max(len(rows) * 2, 20),
            ):
                key = (data.get("linked_reference_doctype"), data.get("linked_reference_name"))
                if key not in pending:
                    continue
                apply(by_key[key], data)
                pending.discard(key)

    crm_keys = [key for key in pending if key[0] == "CRM Lead"]
    if crm_keys and conversation_meta.has_field("linked_crm_lead"):
        for data in frappe.get_all(
            "Chat Conversation",
            filters={"linked_crm_lead": ["in", [key[1] for key in crm_keys]]},
            fields=fields + ["linked_crm_lead"],
            order_by="modified desc",
            limit_page_length=max(len(crm_keys) * 2, 20),
        ):
            key = ("CRM Lead", data.get("linked_crm_lead"))
            if key not in pending:
                continue
            apply(by_key[key], data)
            pending.discard(key)

    if pending and frappe.db.exists("DocType", "Chat Contact") and conversation_meta.has_field("contact"):
        phone_to_keys: dict[str, list[tuple[str, str]]] = {}
        for key in pending:
            row = by_key[key]
            for phone in _whatsapp_phone_candidates(row.get("phone")):
                phone_to_keys.setdefault(phone, []).append(key)
        if phone_to_keys:
            contacts = frappe.get_all(
                "Chat Contact",
                filters={"phone_number": ["in", list(phone_to_keys)]},
                fields=["name", "phone_number"],
                limit_page_length=max(len(phone_to_keys), 20),
            )
            contact_to_keys = {}
            for contact in contacts:
                keys = phone_to_keys.get(contact.get("phone_number")) or []
                if keys:
                    contact_to_keys[contact.get("name")] = keys
            if contact_to_keys:
                for data in frappe.get_all(
                    "Chat Conversation",
                    filters={"contact": ["in", list(contact_to_keys)]},
                    fields=fields,
                    order_by="modified desc",
                    limit_page_length=max(len(contact_to_keys) * 2, 20),
                ):
                    for key in contact_to_keys.get(data.get("contact"), []):
                        if key not in pending:
                            continue
                        apply(by_key[key], data)
                        pending.discard(key)
    return _sort_queue_by_whatsapp(rows, sort_by)


def _attach_queue_missed_calls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows or not frappe.db.exists("DocType", "Vobiz Call Log"):
        return rows

    for row in rows:
        row.update({
            "missed_call_count": 0,
            "missed_call_status": "",
            "missed_call_time": None,
        })

    names_by_doctype: dict[str, list[str]] = {}
    by_key = {}
    for row in rows:
        doctype = row.get("doctype")
        name = row.get("name")
        if not doctype or not name:
            continue
        names_by_doctype.setdefault(doctype, []).append(name)
        by_key[(doctype, name)] = row

    missed_statuses = sorted(MISSED_STATUSES | {"Canceled"})
    seen_logs = set()

    def apply(call, key: tuple[str, str]) -> None:
        if call.name in seen_logs or key not in by_key:
            return
        if not is_inbound_missed_call(call):
            return
        seen_logs.add(call.name)
        row = by_key[key]
        row["missed_call_count"] = frappe.utils.cint(row.get("missed_call_count")) + 1
        call_time = call.get("start_time") or call.get("creation") or call.get("modified")
        current_time = row.get("missed_call_time")
        if not current_time or str(call_time or "") > str(current_time or ""):
            row.setdefault("record_modified", row.get("modified"))
            row["modified"] = call_time
            row["missed_call_time"] = call_time
            row["missed_call_status"] = call.get("status") or ""

    fields = [
        "name",
        "reference_doctype",
        "reference_name",
        "direction",
        "status",
        "call_status",
        "dial_status",
        "hangup_cause",
        "error_message",
        "duration",
        "billsec",
        "recording_duration",
        "start_time",
        "creation",
        "modified",
    ]
    call_meta = frappe.get_meta("Vobiz Call Log")
    for doctype, names in names_by_doctype.items():
        for call in frappe.get_all(
            "Vobiz Call Log",
            filters={
                "reference_doctype": doctype,
                "reference_name": ["in", names],
                "direction": "Incoming",
                "status": ["in", missed_statuses],
            },
            fields=fields,
            order_by="creation desc",
            limit_page_length=0,
        ):
            apply(call, (call.get("reference_doctype"), call.get("reference_name")))

        link_field = "crm_lead" if doctype == "CRM Lead" else ("patient" if doctype == "Patient" else "")
        if not link_field or not call_meta.has_field(link_field):
            continue
        link_fields = fields + [link_field]
        for call in frappe.get_all(
            "Vobiz Call Log",
            filters={
                link_field: ["in", names],
                "direction": "Incoming",
                "status": ["in", missed_statuses],
            },
            fields=link_fields,
            order_by="creation desc",
            limit_page_length=0,
        ):
            apply(call, (doctype, call.get(link_field)))

    return rows


def _sort_queue_by_missed_calls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[int, str]:
        return (
            1 if frappe.utils.cint(row.get("missed_call_count")) else 0,
            str(row.get("missed_call_time") or ""),
        )

    return sorted(rows, key=key, reverse=True)


@frappe.whitelist()
def get_reference_missed_calls(reference_doctype: str, reference_name: str, limit: int | str = 50) -> dict[str, Any]:
    _get_permitted_reference(reference_doctype, reference_name)
    limit = max(1, min(frappe.utils.cint(limit) or 50, 100))
    calls = _reference_missed_call_rows(reference_doctype, reference_name, limit)
    return {
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "count": len(calls),
        "calls": calls,
    }


def _reference_missed_call_rows(reference_doctype: str, reference_name: str, limit: int) -> list[dict[str, Any]]:
    if not reference_doctype or not reference_name or not frappe.db.exists("DocType", "Vobiz Call Log"):
        return []

    fields = [
        "name",
        "reference_doctype",
        "reference_name",
        "direction",
        "status",
        "call_status",
        "dial_status",
        "hangup_cause",
        "error_message",
        "customer_number",
        "did_number",
        "agent_number",
        "user",
        "user_mobile",
        "duration",
        "billsec",
        "recording_duration",
        "start_time",
        "creation",
        "modified",
    ]
    missed_statuses = sorted(MISSED_STATUSES | {"Canceled"})
    filters_list = [
        {
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "direction": "Incoming",
            "status": ["in", missed_statuses],
        }
    ]
    call_meta = frappe.get_meta("Vobiz Call Log")
    link_field = "crm_lead" if reference_doctype == "CRM Lead" else ("patient" if reference_doctype == "Patient" else "")
    if link_field and call_meta.has_field(link_field):
        filters_list.append({
            link_field: reference_name,
            "direction": "Incoming",
            "status": ["in", missed_statuses],
        })

    rows = []
    seen = set()
    for filters in filters_list:
        query_fields = fields + ([link_field] if link_field and link_field in filters and link_field not in fields else [])
        for call in frappe.get_all(
            "Vobiz Call Log",
            filters=filters,
            fields=query_fields,
            order_by="creation desc",
            limit_page_length=limit,
        ):
            if call.name in seen or not is_inbound_missed_call(call):
                continue
            seen.add(call.name)
            call["duration_label"] = _duration_label(_talk_seconds(call))
            call["recording_download_url"] = recording_proxy_url(call.name) if call.get("recording_url") else ""
            rows.append(call)
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break
    rows.sort(key=lambda row: str(row.get("start_time") or row.get("creation") or row.get("modified") or ""), reverse=True)
    return rows[:limit]


def _sort_queue_by_whatsapp(rows: list[dict[str, Any]], sort_by: str | None = None) -> list[dict[str, Any]]:
    if sort_by != "whatsapp_unread_desc":
        return rows

    def key(row: dict[str, Any]) -> tuple[int, str]:
        return (
            frappe.utils.cint(row.get("whatsapp_unread_count")),
            str(row.get("whatsapp_last_message_at") or ""),
        )

    return sorted(rows, key=key, reverse=True)


def _whatsapp_phone_candidates(phone: str | None) -> list[str]:
    raw = str(phone or "").strip()
    digits = _last_10_digits(raw)
    candidates = []
    for value in (raw, digits, f"+91{digits}" if digits else "", f"91{digits}" if digits else ""):
        if value and value not in candidates:
            candidates.append(value)
    return candidates


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
        row["duration_label"] = _duration_label(_talk_seconds(row))
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
        "patient_followup_status_options": get_patient_followup_status_options_api(),
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
        meta = frappe.get_meta(reference_doctype)
        wanted = (
            "patient_name",
            "first_name",
            "middle_name",
            "last_name",
            "sex",
            "gender",
            "subject",
            "customer",
            "priority",
            "issue_type",
            "description",
            "raised_by",
            "mobile",
            "mobile_no",
            "phone",
            "phone_no",
            "custom_whatsapp_number",
            "email",
            "status",
            "created_by_agent",
        )
        fields = []
        for fieldname in wanted:
            df = meta.get_field(fieldname)
            if not df:
                continue
            fields.append(
                {
                    "fieldname": fieldname,
                    "label": df.label or fieldname,
                    "value": doc.get(fieldname),
                    "fieldtype": df.fieldtype,
                    "options": _status_options(reference_doctype) if fieldname == "status" else [],
                }
            )
        return {"doctype": reference_doctype, "name": reference_name, "fields": fields}

    meta = frappe.get_meta("CRM Lead")
    wa_ai = _conversation_ai_fields(reference_doctype, reference_name) if include_conversation_ai else {}
    wanted = (
        "status",
        "sr_lead_platform",
        "sr_lead_disposition",
        "source",
        "sr_lead_pipeline",
        "team",
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

    phone = _first_value(doc.as_dict(), _phone_field_candidates()).get("value")
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
    connected = len([row for row in history if status_bucket(row) == "connected"])
    missed = len([row for row in history if status_bucket(row) in {"missed", "busy", "no_answer", "failed", "cancelled"}])
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

    if conversation:
        fields = _existing_fields(frappe.get_meta("Chat Conversation"), (
            "name", "status", "priority", "lead_score", "lead_lan", "lead_temperature",
            "last_message_preview", "unread_count", "ai_summary", "modified",
        ))
        data = frappe.db.get_value("Chat Conversation", conversation, fields, as_dict=True) if fields else {"name": conversation}
        page = _whatsapp_messages_page(conversation, 30)
        return {
            "available": True,
            "conversation": conversation,
            "data": data,
            **page,
        }

    return {"available": True, "conversation": None}


def _whatsapp_route_status(reference_doctype: str, reference_name: str) -> dict[str, Any]:
    try:
        doc = frappe.get_doc(reference_doctype, reference_name)
        if reference_doctype == "CRM Lead":
            return _whatsapp_route_status_for_lead(doc)

        patient_name = reference_name if reference_doctype == "Patient" else _resolve_patient(reference_doctype, reference_name, doc)
        if patient_name and frappe.db.exists("Patient", patient_name):
            return _whatsapp_route_status_for_patient(frappe.get_doc("Patient", patient_name))

        return {"available": False, "conversation": None, "message": _("WhatsApp routing is not configured for this record.")}
    except Exception as exc:
        return {"available": False, "conversation": None, "message": str(exc)}


def _whatsapp_route_status_for_lead(lead) -> dict[str, Any]:
    pipeline = lead.get("sr_lead_pipeline")
    if not pipeline:
        return {"available": False, "conversation": None, "message": _("Select a CRM Lead Pipeline before opening WhatsApp chat.")}

    account_route = _interakt_account_for_outbound_pipeline(pipeline)
    if account_route:
        return {"available": True, "channel_account": account_route, "pipeline_map": None}

    if not frappe.db.exists("SR Lead Pipeline", pipeline):
        return {"available": False, "conversation": None, "message": _("Could not find Pipeline: {0}").format(pipeline)}

    from wa_chat_hub.messaging.channel_map import get_pipeline_map

    row = get_pipeline_map(pipeline=pipeline)
    return {"available": True, "channel_account": row.get("chat_channel_account"), "pipeline_map": row.get("name")}


def _interakt_account_for_outbound_pipeline(pipeline: str) -> str | None:
    if not frappe.db.exists("DocType", "Chat Channel Account"):
        return None
    if not frappe.db.exists("DocType", "Chat Channel Account Pipeline"):
        return None

    mapped_accounts = frappe.get_all(
        "Chat Channel Account Pipeline",
        filters={"pipeline": pipeline, "parenttype": "Chat Channel Account"},
        pluck="parent",
        limit_page_length=2,
    )
    if not mapped_accounts:
        return None
    accounts = frappe.get_all(
        "Chat Channel Account",
        filters={
            "name": ["in", mapped_accounts],
            "channel_type": "Interakt",
            "is_active": 1,
        },
        pluck="name",
        limit_page_length=2,
    )
    if len(accounts) > 1:
        frappe.throw(
            _("Multiple active Interakt accounts are configured for Pipeline {0}.").format(pipeline)
        )
    return accounts[0] if accounts else None


def _whatsapp_route_status_for_patient(patient) -> dict[str, Any]:
    department = patient.get("sr_medical_department")
    if not department:
        return {"available": False, "conversation": None, "message": _("Select a Medical Department before opening WhatsApp chat.")}
    if not frappe.db.exists("Medical Department", department):
        return {"available": False, "conversation": None, "message": _("Could not find Medical Department: {0}").format(department)}

    from wa_chat_hub.messaging.channel_map import get_pipeline_map

    row = get_pipeline_map(medical_department=department)
    return {"available": True, "channel_account": row.get("chat_channel_account"), "pipeline_map": row.get("name")}


def _whatsapp_recent_messages(conversation: str) -> list[dict[str, Any]]:
    return _whatsapp_messages_page(conversation, 10).get("messages", [])


def _whatsapp_messages_page(conversation: str, limit: int | str = 30, before: str | None = None) -> dict[str, Any]:
    if not conversation or not frappe.db.exists("DocType", "Chat Message"):
        return {"messages": [], "has_more": False, "next_before": None}

    limit = max(1, min(frappe.utils.cint(limit) or 30, 100))
    meta = frappe.get_meta("Chat Message")
    fields = _existing_fields(meta, (
        "name",
        "direction",
        "sender_type",
        "content_type",
        "body",
        "media_url",
        "attachment_file",
        "delivery_status",
        "creation",
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
    for row in rows:
        if row.get("attachment_file") and not row.get("media_url"):
            row["attachment_url"] = frappe.db.get_value("File", row.get("attachment_file"), "file_url") or ""
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
    phone = _first_value(doc.as_dict(), _phone_field_candidates()).get("value")
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


def _phone_field_candidates() -> tuple[str, ...]:
    return (
        "sr_pe_mobile",
        "mobile_no",
        "mobile",
        "phone",
        "phone_no",
        "contact_mobile",
        "contact_phone",
        "whatsapp_no",
        "sr_mobile_no",
        "sr_whatsapp_no",
        "alternate_phone",
    )


def _status_options(doctype: str) -> list[str]:
    if not doctype or not frappe.db.exists("DocType", doctype):
        return []
    field = frappe.get_meta(doctype).get_field("status")
    if not field:
        return []
    return [row.strip() for row in (field.options or "").splitlines() if row.strip()]


def _linked_customer_phone(customer: str | None) -> dict[str, Any]:
    if not customer or not frappe.db.exists("DocType", "Customer") or not frappe.db.exists("Customer", customer):
        return {"fieldname": None, "value": None}
    meta = frappe.get_meta("Customer")
    fields = [fieldname for fieldname in _phone_field_candidates() if meta.has_field(fieldname)]
    if fields:
        row = frappe.db.get_value("Customer", customer, fields, as_dict=True) or {}
        for fieldname in fields:
            if row.get(fieldname):
                return {"fieldname": None, "value": row.get(fieldname)}
    contact_phone = _linked_customer_contact_phone(customer)
    if contact_phone:
        return {"fieldname": None, "value": contact_phone}
    return {"fieldname": None, "value": None}


def _linked_customer_contact_phone(customer: str) -> str | None:
    if not frappe.db.exists("DocType", "Contact") or not frappe.db.exists("DocType", "Dynamic Link"):
        return None
    contact_meta = frappe.get_meta("Contact")
    fields = [fieldname for fieldname in _phone_field_candidates() if contact_meta.has_field(fieldname)]
    if not fields:
        return None
    rows = frappe.get_all(
        "Dynamic Link",
        filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Contact"},
        fields=["parent"],
        order_by="modified desc",
        limit_page_length=20,
    )
    for row in rows:
        contact = frappe.db.get_value("Contact", row.parent, fields, as_dict=True) or {}
        for fieldname in fields:
            if contact.get(fieldname):
                return contact.get(fieldname)
    return None


def _analytics_filter_values(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        try:
            parsed = frappe.parse_json(text)
            raw_values = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            raw_values = text.replace(",", "\n").splitlines()

    cleaned = []
    seen = set()
    for item in raw_values:
        item = str(item or "").strip()
        if item and item not in seen:
            cleaned.append(item)
            seen.add(item)
    return cleaned


def _duration_label(seconds: int) -> str:
    seconds = frappe.utils.cint(seconds)
    minutes, remainder = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {remainder:02d}s"
    return f"{remainder}s"


def _talk_seconds(row) -> int:
    data = row.as_dict() if callable(getattr(row, "as_dict", None)) else dict(row or {})
    return talk_seconds(data)


def _list_from_template_values(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = frappe.parse_json(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item or "").strip()]
    except Exception:
        pass
    return [row.strip() for row in text.replace(",", "\n").splitlines() if row.strip()]
