from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from vobiz_ai.api.call_log import create_outbound_call_log, sync_linked_summaries
from vobiz_click_to_call.api.recording import recording_proxy_url
from vobiz_click_to_call.services.call_log_update import save_doc_latest, snapshot_doc
from vobiz_click_to_call.services.client import VobizClient, extract_provider_id
from vobiz_click_to_call.services.call_status import status_from_provider
from vobiz_click_to_call.services.debug_log import log_vobiz_event
from vobiz_click_to_call.services.disposition import update_reference_call_metrics
from vobiz_click_to_call.services.numbers import mask_phone, normalize_phone_number, numbers_match
from vobiz_click_to_call.services.safety import assert_call_allowed, get_working_hours_block_reason
from vobiz_click_to_call.services.settings import (
    build_callback_url,
    get_allowed_doctypes,
    get_caller_id,
    get_default_country_code,
    get_idle_auto_offline_config,
    get_settings,
)

PHONE_FIELD_HINTS = (
    "mobile",
    "phone",
    "contact",
    "whatsapp",
)

PREFERRED_PHONE_FIELDS = (
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

TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}
USER_SET_AVAILABILITY_STATUSES = {"Available", "Away", "Offline"}
AGENT_ATTENDANCE_DOCTYPE = "Vobiz Agent Attendance Log"
AVAILABILITY_ATTENDANCE_SOURCE = "Availability"
AVAILABILITY_ATTENDANCE_TAB = "__availability__"
AGENT_ATTENDANCE_TZ = ZoneInfo("Asia/Kolkata")
STALE_STARTUP_STATUSES = {"Queued", "Initiated", "Dialing", "Ringing", "Connecting"}
STALE_STARTUP_CALL_SECONDS = 10 * 60
IDLE_AUTO_OFFLINE_EXEMPT_ROLES = {
    "System Manager",
    "Manager",
    "Vobiz Manager",
    "Call Center Manager",
    "Sales Manager",
    "Team Manager",
    "Team Leader",
}


@frappe.whitelist()
def get_allowed_doctypes_api() -> list[str]:
    if frappe.session.user == "Guest":
        return []
    return sorted(get_allowed_doctypes(get_settings()))


@frappe.whitelist()
def get_call_capability(reference_doctype: str | None = None, reference_name: str | None = None) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        return {"can_call": False, "reason": _("Login required.")}

    if not frappe.db.exists("DocType", "Vobiz Settings"):
        return {"can_call": False, "reason": _("Vobiz Click To Call is not installed.")}

    settings = get_settings()
    if not settings.enabled:
        return {"can_call": False, "reason": _("Vobiz Click To Call is disabled.")}

    mapping = get_user_mapping(frappe.session.user)
    if not mapping:
        return {"can_call": False, "reason": _("No active Vobiz user mapping found.")}

    unavailable_reason = get_mapping_unavailable_reason(mapping)
    if unavailable_reason:
        return {
            "can_call": False,
            "reason": unavailable_reason,
            "availability_status": mapping.get("availability_status") or "Available",
        }

    if reference_doctype and reference_name:
        if reference_doctype not in get_allowed_doctypes(settings):
            return {"can_call": False, "reason": _("Calling is not enabled for this DocType.")}

        if not frappe.db.exists(reference_doctype, reference_name):
            return {"can_call": False, "reason": _("Document not found.")}

        doc = frappe.get_doc(reference_doctype, reference_name)
        if not doc.has_permission("read") and not has_mapped_patient_access(reference_doctype, reference_name):
            return {"can_call": False, "reason": _("You do not have permission to read this document.")}

    return {
        "can_call": True,
        "agent_mobile": mapping["agent_mobile"],
        "agent_mobile_display": mask_phone(mapping["agent_mobile"]),
        "availability_status": mapping.get("availability_status") or "Available",
    }


def _patient_primary_phone_candidates(doc, default_country_code: str) -> list[dict[str, str]]:
    candidates = []
    seen = set()
    for fieldname, label in (("mobile", _("Mobile Number")), ("phone", _("Contact Number"))):
        if not doc.meta.has_field(fieldname):
            continue
        number = str(doc.get(fieldname) or "").strip()
        normalized = normalize_phone_number(number, default_country_code=default_country_code)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append({"fieldname": fieldname, "label": label, "number": number})
    return candidates


@frappe.whitelist()
def get_patient_phone_choices(patient: str) -> list[dict[str, str]]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if not patient or not frappe.db.exists("Patient", patient):
        frappe.throw(_("Patient not found."))
    doc = frappe.get_doc("Patient", patient)
    if not doc.has_permission("read") and not has_mapped_patient_access("Patient", patient):
        frappe.throw(_("You do not have permission to read this patient."))
    return _patient_primary_phone_candidates(doc, get_default_country_code(get_settings()))


@frappe.whitelist()
def start_call(
    reference_doctype: str,
    reference_name: str,
    phone_field: str | None = None,
    phone_number: str | None = None,
    patient_phone_selected: int | str = 0,
) -> dict[str, Any]:
    log_vobiz_event(
        "Start call requested",
        payload={
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "phone_field": phone_field,
            "phone_number": phone_number,
            "user": frappe.session.user,
        },
    )
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    settings = get_settings()
    if not settings.enabled:
        frappe.throw(_("Vobiz Click To Call is disabled."))

    allowed_doctypes = get_allowed_doctypes(settings)
    if reference_doctype not in allowed_doctypes:
        frappe.throw(_("Calling is not enabled for {0}.").format(reference_doctype))

    if not frappe.db.exists(reference_doctype, reference_name):
        frappe.throw(_("{0} {1} was not found.").format(reference_doctype, reference_name))

    doc = frappe.get_doc(reference_doctype, reference_name)
    if not doc.has_permission("read") and not has_mapped_patient_access(reference_doctype, reference_name):
        frappe.throw(_("You do not have permission to call from this document."))
    if reference_doctype == "Patient":
        choices = _patient_primary_phone_candidates(doc, get_default_country_code(settings))
        if len(choices) > 1 and not frappe.utils.cint(patient_phone_selected):
            frappe.throw(_("Select the Patient number to call."))
    mapping = get_user_mapping(frappe.session.user)
    if not mapping:
        frappe.throw(_("No active Vobiz user mapping found for your user."))
    unavailable_reason = get_mapping_unavailable_reason(mapping)
    if unavailable_reason:
        frappe.throw(unavailable_reason)

    default_country_code = get_default_country_code(settings)
    raw_customer_number, resolved_phone_field = resolve_target_number(doc, phone_field, phone_number)
    customer_number = normalize_phone_number(raw_customer_number, default_country_code=default_country_code)
    user_mobile = normalize_phone_number(mapping["agent_mobile"], default_country_code=default_country_code)
    caller_id = get_caller_id(settings, mapping)
    default_caller_id = get_caller_id(settings, {})
    if caller_id == user_mobile and default_caller_id and default_caller_id != user_mobile:
        caller_id = default_caller_id

    if not customer_number:
        frappe.throw(_("Customer phone number is required."))
    if not user_mobile:
        frappe.throw(_("Your mapped Vobiz agent mobile number is missing."))
    if not caller_id:
        frappe.throw(_("Vobiz caller ID is not configured."))

    assert_call_allowed(
        customer_number=customer_number,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        user=frappe.session.user,
        mapping=mapping,
        settings=settings,
    )

    call_flow = settings.default_call_flow or "Customer First"
    call_log = create_call_log(
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        phone_field=resolved_phone_field,
        customer_number=customer_number,
        user_mobile=user_mobile,
        caller_id=caller_id,
        call_flow=call_flow,
    )
    log_vobiz_event(
        "Call log created",
        call_log=call_log.name,
        payload={
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "customer_number": customer_number,
            "user_mobile": user_mobile,
            "caller_id": caller_id,
            "call_flow": call_flow,
        },
    )
    mark_mapping_busy(mapping["name"], call_log.name)

    token = call_log.callback_token
    first_leg_number = user_mobile if call_flow == "Agent First" else customer_number
    payload = {
        "from": caller_id,
        "to": first_leg_number,
        "answer_url": build_callback_url("vobiz_click_to_call.api.webhook.answer", call_log.name, token, settings),
        "answer_method": "POST",
        "ring_url": build_callback_url("vobiz_click_to_call.api.webhook.ring", call_log.name, token, settings),
        "ring_method": "POST",
        "hangup_url": build_callback_url("vobiz_click_to_call.api.webhook.hangup", call_log.name, token, settings),
        "hangup_method": "POST",
        "fallback_url": build_callback_url("vobiz_click_to_call.api.webhook.fallback", call_log.name, token, settings),
        "fallback_method": "POST",
    }

    if settings.max_call_duration:
        payload["time_limit"] = int(settings.max_call_duration)

    before = snapshot_doc(call_log)
    call_log.request_json = json.dumps(redact_callback_tokens(payload), indent=2)
    call_log = save_doc_latest(call_log, before)
    log_vobiz_event("Provider make_call payload prepared", call_log=call_log.name, payload=redact_callback_tokens(payload))
    frappe.db.commit()

    try:
        response = VobizClient(settings).make_call(payload)
    except Exception as exc:
        if _is_unowned_from_number_error(exc) and default_caller_id and caller_id != default_caller_id:
            caller_id = default_caller_id
            payload["from"] = caller_id
            before = snapshot_doc(call_log)
            call_log.caller_id = caller_id
            call_log.did_number = caller_id
            call_log.normalized_did = caller_id
            call_log.request_json = json.dumps(redact_callback_tokens(payload), indent=2)
            call_log.error_message = ""
            call_log = save_doc_latest(call_log, before)
            log_vobiz_event(
                "Provider rejected mapped caller ID; retrying with default caller ID",
                call_log=call_log.name,
                severity="Warning",
                payload=redact_callback_tokens(payload),
            )
            frappe.db.commit()
            try:
                response = VobizClient(settings).make_call(payload)
            except Exception as retry_exc:
                _fail_provider_call(call_log, retry_exc)
                raise
        else:
            _fail_provider_call(call_log, exc)
            raise

    before = snapshot_doc(call_log)
    call_log.response_json = json.dumps(response, indent=2, default=str)
    call_log.request_uuid = extract_provider_id(response, "request_uuid", "requestUUID", "request_id", "requestId")
    call_log.call_uuid = extract_provider_id(response, "call_uuid", "callUUID", "uuid", "CallUUID")
    call_log = save_doc_latest(call_log, before)
    update_reference_call_metrics(reference_doctype, reference_name)
    sync_linked_summaries(call_log)
    log_vobiz_event("Provider make_call response received", call_log=call_log.name, payload=response)
    frappe.db.commit()

    return {
        "call_log": call_log.name,
        "status": call_log.status,
        "call_flow": call_flow,
        "customer_number": customer_number,
        "agent_mobile_display": mask_phone(user_mobile),
    }


def _is_unowned_from_number_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "from number" in message and "not owned" in message


def _fail_provider_call(call_log, exc: Exception) -> None:
    before = snapshot_doc(call_log)
    call_log.status = "Failed"
    call_log.error_message = str(exc)
    call_log = save_doc_latest(call_log, before)
    restore_mapping_after_call(call_log.name)
    frappe.db.commit()
    log_vobiz_event(
        "Provider make_call failed",
        call_log=call_log.name,
        severity="Error",
        payload={"error": str(exc)},
        traceback=frappe.get_traceback(),
    )


@frappe.whitelist()
def get_call_status(call_log: str) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if "System Manager" not in frappe.get_roles() and doc.user != frappe.session.user:
        frappe.throw(_("Not permitted."))

    sync_live_call_if_finished(doc)

    return {
        "name": doc.name,
        "status": doc.status,
        "reference_doctype": doc.reference_doctype,
        "reference_name": doc.reference_name,
        "reference_title": get_reference_title(doc.reference_doctype, doc.reference_name),
        "call_status": doc.call_status,
        "dial_status": doc.dial_status,
        "hangup_cause": doc.hangup_cause,
        "error_message": doc.error_message,
        "recording_status": doc.recording_status,
        "recording_error": doc.recording_error,
        "transcript_status": doc.transcript_status,
        "transcript_text": doc.transcript_text or doc.transcription_text,
        "transcript_error": doc.transcript_error,
        "ai_disposition_status": doc.ai_disposition_status,
        "ai_disposition": doc.ai_disposition,
        "ai_confidence": doc.ai_confidence,
        "ai_summary": doc.ai_summary,
        "ai_next_action": doc.ai_next_action,
        "disposition": doc.disposition,
        "disposition_notes": doc.disposition_notes,
        "recording_url": doc.recording_url,
        "recording_download_url": recording_proxy_url(doc.name) if doc.recording_url else "",
        "customer_number_display": mask_phone(doc.customer_number),
        "agent_mobile_display": mask_phone(doc.user_mobile),
        "call_flow": doc.call_flow,
        "answer_time": doc.answer_time,
        "start_time": doc.start_time,
        "end_time": doc.end_time,
        "duration": doc.duration,
        "billsec": doc.billsec,
        "can_cancel": doc.status not in TERMINAL_STATUSES,
    }


def sync_live_call_if_finished(doc) -> None:
    if doc.status in TERMINAL_STATUSES:
        return
    call_uuid = doc.call_uuid or doc.a_leg_uuid or doc.b_leg_uuid
    if not call_uuid:
        return

    try:
        response = VobizClient(get_settings()).retrieve_live_call(call_uuid)
    except Exception as exc:
        message = str(exc).lower()
        if "not found" not in message and "call not found" not in message:
            return
        before = snapshot_doc(doc)
        doc.status = status_from_provider(
            {"status": "completed", "call_status": "not found", "duration": doc.duration, "billsec": doc.billsec},
            previous=doc.status,
        ) or ("Completed" if doc.status == "Connected" else "Cancelled")
        doc.call_status = doc.call_status or "ended"
        doc.hangup_cause = doc.hangup_cause or "LIVE_CALL_NOT_FOUND"
        doc.end_time = doc.end_time or frappe.utils.now()
        doc.response_json = merge_json(doc.response_json, {"live_status_warning": str(exc)})
        doc = save_doc_latest(doc, before)
        restore_mapping_after_call(doc.name)
        update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
        frappe.db.commit()
        return

    provider_status = _provider_live_status(response)
    if provider_status in {"completed", "hangup", "ended", "failed", "busy", "no-answer", "no answer", "cancelled", "canceled"}:
        before = snapshot_doc(doc)
        doc.status = _terminal_status_from_provider(provider_status, doc.status, doc)
        doc.call_status = provider_status
        doc.end_time = doc.end_time or frappe.utils.now()
        doc.response_json = merge_json(doc.response_json, {"live_status_response": response})
        doc = save_doc_latest(doc, before)
        restore_mapping_after_call(doc.name)
        update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
        frappe.db.commit()


def _provider_live_status(response: dict[str, Any] | None) -> str:
    if not isinstance(response, dict):
        return ""
    for source in (response, response.get("data") if isinstance(response.get("data"), dict) else {}):
        for key in ("status", "call_status", "CallStatus", "state"):
            value = source.get(key)
            if value not in (None, ""):
                return str(value).strip().lower().replace("_", "-")
    return ""


def _terminal_status_from_provider(provider_status: str, current_status: str, doc=None) -> str:
    return status_from_provider(
        {
            "status": provider_status,
            "duration": getattr(doc, "duration", 0) if doc else 0,
            "billsec": getattr(doc, "billsec", 0) if doc else 0,
        },
        previous=current_status,
    ) or current_status or "Completed"


def get_reference_title(reference_doctype: str | None, reference_name: str | None) -> str:
    if not reference_doctype or not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        return reference_name or ""
    meta = frappe.get_meta(reference_doctype)
    fields = []
    for fieldname in ("patient_name", "lead_name", "customer_name", "first_name", "company_name", "title"):
        if meta.has_field(fieldname):
            fields.append(fieldname)
    if not fields:
        return reference_name
    row = frappe.db.get_value(reference_doctype, reference_name, fields, as_dict=True) or {}
    for fieldname in fields:
        if row.get(fieldname):
            return row.get(fieldname)
    return reference_name


@frappe.whitelist()
def cancel_call(call_log: str) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    log_vobiz_event("Cancel call requested", call_log=doc.name, payload={"user": frappe.session.user, "status": doc.status})
    if "System Manager" not in frappe.get_roles() and doc.user != frappe.session.user:
        frappe.throw(_("Not permitted."))

    if doc.status in TERMINAL_STATUSES:
        return {"status": doc.status, "message": _("Call is already finished.")}

    call_uuid = doc.call_uuid or doc.a_leg_uuid or doc.b_leg_uuid
    if call_uuid:
        try:
            response = VobizClient(get_settings()).hangup_call(call_uuid)
            message = _("Call cancellation requested.")
            log_vobiz_event("Provider hangup response received", call_log=doc.name, payload=response)
        except Exception as exc:
            error_text = str(exc)
            if "call not found" not in error_text.lower() and "not found" not in error_text.lower():
                log_vobiz_event(
                    "Provider hangup failed",
                    call_log=doc.name,
                    severity="Error",
                    payload={"error": error_text, "call_uuid": call_uuid},
                    traceback=frappe.get_traceback(),
                )
                raise
            response = {
                "provider_cancel_warning": error_text,
                "treated_as_ended": True,
                "call_uuid": call_uuid,
            }
            message = _("Provider call was already ended. Local call was cleared.")
            log_vobiz_event("Provider hangup call not found; cancelled locally", call_log=doc.name, severity="Warning", payload=response)
    else:
        response = {"skipped_provider_cancel": True, "reason": "Provider call UUID was not available."}
        message = _("Queued call cleared locally.")
        log_vobiz_event("Cancel skipped provider hangup; no UUID", call_log=doc.name, severity="Warning", payload=response)

    before = snapshot_doc(doc)
    doc.status = "Cancelled"
    doc.error_message = "Call cancelled by user."
    doc.end_time = doc.end_time or frappe.utils.now()
    doc.response_json = merge_json(doc.response_json, {"cancel_response": response})
    doc = save_doc_latest(doc, before)
    restore_mapping_after_call(doc.name)
    update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
    frappe.db.commit()
    return {"status": doc.status, "message": message}


@frappe.whitelist()
def get_my_availability() -> dict[str, Any]:
    if frappe.session.user == "Guest":
        return {"is_mapped": False, "reason": _("Login required.")}

    mapping = get_user_mapping(frappe.session.user)
    if not mapping:
        return {"is_mapped": False, "reason": _("No active Vobiz user mapping found.")}
    idle_auto_offline = get_idle_auto_offline_config()

    last_status_at = mapping.get("last_status_at")
    last_status_epoch_ms = 0
    if last_status_at:
        last_status_dt = frappe.utils.get_datetime(last_status_at)
        if last_status_dt.tzinfo is None:
            last_status_dt = last_status_dt.replace(tzinfo=AGENT_ATTENDANCE_TZ)
        last_status_epoch_ms = int(last_status_dt.timestamp() * 1000)

    return {
        "is_mapped": True,
        "accept_calls": bool(frappe.utils.cint(mapping.get("accept_calls"))),
        "availability_status": mapping.get("availability_status") or "Available",
        "agent_mobile_display": mask_phone(mapping.get("agent_mobile")),
        "current_call_log": mapping.get("current_call_log"),
        "last_status_at": frappe.utils.format_datetime(last_status_at) if last_status_at else "",
        "last_status_epoch_ms": last_status_epoch_ms,
        "idle_auto_offline_enabled": bool(idle_auto_offline.get("enabled")) and _should_apply_idle_auto_offline(frappe.session.user),
        "idle_auto_offline_seconds": idle_auto_offline.get("seconds") or 0,
        "idle_auto_offline_minutes": idle_auto_offline.get("minutes") or 0,
        "reason": get_mapping_unavailable_reason(mapping) or "",
    }


@frappe.whitelist()
def set_my_availability(status: str) -> dict[str, Any]:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    status = (status or "").strip()
    if status not in USER_SET_AVAILABILITY_STATUSES:
        frappe.throw(_("Invalid availability status."))

    mapping = get_user_mapping(frappe.session.user)
    if not mapping:
        frappe.throw(_("No active Vobiz user mapping found."))

    current_call_log = mapping.get("current_call_log")
    if current_call_log and is_active_call_log(current_call_log):
        frappe.throw(_("You cannot change availability while a Vobiz call is active."))

    accept_calls = 1 if status == "Available" else 0
    frappe.db.set_value(
        "Vobiz User Mapping",
        mapping["name"],
        {
            "availability_status": status,
            "accept_calls": accept_calls,
            "current_call_log": "",
            "last_status_at": frappe.utils.now(),
        },
        update_modified=True,
    )
    _record_availability_attendance(frappe.session.user, status)
    frappe.db.commit()

    return get_my_availability()


def _record_availability_attendance(user: str, status: str) -> None:
    if not user or not frappe.db.exists("DocType", AGENT_ATTENDANCE_DOCTYPE):
        return

    meta = frappe.get_meta(AGENT_ATTENDANCE_DOCTYPE)
    if not meta.has_field("availability_status"):
        return

    now = datetime.now(AGENT_ATTENDANCE_TZ).replace(tzinfo=None)
    shift_date = str(now.date())
    rows = frappe.get_all(
        AGENT_ATTENDANCE_DOCTYPE,
        filters={
            "agent_user": user,
            "tab_id": AVAILABILITY_ATTENDANCE_TAB,
            "shift_date": shift_date,
            "status": "Open",
            "source": AVAILABILITY_ATTENDANCE_SOURCE,
        },
        fields=["name", "online_from", "availability_status"],
        order_by="creation desc",
        limit_page_length=20,
    )
    for row in rows:
        if row.availability_status == status:
            frappe.db.set_value(
                AGENT_ATTENDANCE_DOCTYPE,
                row.name,
                {"last_seen_at": now},
                update_modified=False,
            )
            return
        started_at = frappe.utils.get_datetime(row.online_from)
        frappe.db.set_value(
            AGENT_ATTENDANCE_DOCTYPE,
            row.name,
            {
                "status": "Closed",
                "last_seen_at": now,
                "offline_at": now,
                "duration_seconds": max(0, int((now - started_at).total_seconds())),
            },
            update_modified=False,
        )

    frappe.get_doc(
        {
            "doctype": AGENT_ATTENDANCE_DOCTYPE,
            "agent_user": user,
            "tab_id": AVAILABILITY_ATTENDANCE_TAB,
            "status": "Open",
            "shift_date": shift_date,
            "online_from": now,
            "last_seen_at": now,
            "duration_seconds": 0,
            "availability_status": status,
            "source": AVAILABILITY_ATTENDANCE_SOURCE,
        }
    ).insert(ignore_permissions=True)


def get_user_mapping(user: str) -> dict[str, Any] | None:
    if not frappe.db.exists("DocType", "Vobiz User Mapping"):
        return None

    meta = frappe.get_meta("Vobiz User Mapping")
    rows = frappe.get_all(
        "Vobiz User Mapping",
        filters={"user": user, "enabled": 1},
        fields=_existing_mapping_fields(meta, [
            "name",
            "user",
            "agent_mobile",
            "caller_id",
            "accept_calls",
            "availability_status",
            "auto_available_after_call",
            "current_call_log",
            "enforce_working_hours",
            "working_hours_start",
            "working_hours_end",
            "working_days",
            "team",
            "team_leader",
            "pipeline",
            "queue_source",
            "fallback_user",
            "fallback_users",
            "sr_medical_department",
            "sr_medical_departments",
            "sr_followup_id",
            "sr_followup_ids",
        ]),
        limit=1,
    )
    return rows[0] if rows else None


def _should_apply_idle_auto_offline(user: str | None) -> bool:
    user = (user or "").strip()
    if not user or user == "Administrator":
        return False
    roles = set(frappe.get_roles(user))
    if roles.intersection(IDLE_AUTO_OFFLINE_EXEMPT_ROLES):
        return False
    if _is_vobiz_team_leader(user):
        return False
    return True


def _is_vobiz_team_leader(user: str) -> bool:
    if not user:
        return False
    try:
        if frappe.db.exists("DocType", "Team") and frappe.db.exists("DocType", "Team User"):
            if frappe.db.exists("Team", {"team_lead": user, "is_active": 1}):
                return True
        if frappe.db.exists("DocType", "Vobiz User Mapping"):
            return bool(
                frappe.db.exists(
                    "Vobiz User Mapping",
                    {"enabled": 1, "team_leader": user},
                )
            )
    except Exception:
        return False
    return False


def _existing_mapping_fields(meta, fields: list[str]) -> list[str]:
    return [fieldname for fieldname in fields if fieldname == "name" or meta.has_field(fieldname)]


def has_mapped_patient_access(reference_doctype: str, reference_name: str, user: str | None = None) -> bool:
    if reference_doctype != "Patient" or not frappe.db.exists("DocType", "Patient"):
        return False
    mapping = get_user_mapping(user or frappe.session.user)
    if not mapping or (mapping.get("queue_source") or "").strip() not in {"Patient", "CRM Lead and Patient"}:
        return False
    meta = frappe.get_meta("Patient")
    filters: dict[str, Any] = {"name": reference_name}
    if meta.has_field("sr_medical_department"):
        departments = _split_mapping_values(mapping.get("sr_medical_departments"), first=mapping.get("sr_medical_department"))
        if not departments:
            return False
        filters["sr_medical_department"] = ["in", departments]
    if meta.has_field("sr_followup_id"):
        followup_ids = _split_mapping_values(mapping.get("sr_followup_ids"), first=mapping.get("sr_followup_id"))
        if not followup_ids:
            return False
        filters["sr_followup_id"] = ["in", followup_ids]
    return bool(frappe.db.exists("Patient", filters))


def _split_mapping_values(value: str | None, first: str | None = None) -> list[str]:
    values = []
    seen = set()
    for raw in [first or "", value or ""]:
        for row in str(raw).replace(",", "\n").splitlines():
            row = row.strip()
            if row and row not in seen:
                values.append(row)
                seen.add(row)
    return values


def get_mapping_unavailable_reason(mapping: dict[str, Any]) -> str:
    current_call_log = mapping.get("current_call_log")
    if current_call_log and not is_active_call_log(current_call_log):
        if frappe.db.exists("Vobiz Call Log", current_call_log):
            restore_mapping_after_call(current_call_log)
        else:
            auto_available = frappe.utils.cint(mapping.get("auto_available_after_call"))
            frappe.db.set_value(
                "Vobiz User Mapping",
                mapping["name"],
                {
                    "availability_status": "Available" if auto_available else "Away",
                    "accept_calls": 1 if auto_available else 0,
                    "current_call_log": "",
                    "last_status_at": frappe.utils.now(),
                },
                update_modified=True,
            )
        frappe.db.commit()
        if frappe.utils.cint(mapping.get("auto_available_after_call")):
            mapping["availability_status"] = "Available"
            mapping["accept_calls"] = 1
            mapping["current_call_log"] = ""
        else:
            mapping["availability_status"] = "Away"
            mapping["accept_calls"] = 0
            mapping["current_call_log"] = ""

    status = mapping.get("availability_status") or "Available"
    if status != "Available":
        return _("Your Vobiz availability is {0}.").format(status)

    if not frappe.utils.cint(mapping.get("accept_calls")):
        return _("You are not accepting Vobiz calls.")

    if current_call_log and is_active_call_log(current_call_log):
        return _("You already have an active Vobiz call.")

    working_hours_reason = get_working_hours_block_reason(mapping)
    if working_hours_reason:
        return working_hours_reason

    return ""


def is_active_call_log(call_log: str | None) -> bool:
    if not call_log or not frappe.db.exists("Vobiz Call Log", call_log):
        return False

    row = frappe.db.get_value("Vobiz Call Log", call_log, ["status", "modified"], as_dict=True)
    if not row or row.status in TERMINAL_STATUSES:
        return False
    if row.status in STALE_STARTUP_STATUSES and row.modified:
        age_seconds = (frappe.utils.now_datetime() - frappe.utils.get_datetime(row.modified)).total_seconds()
        if age_seconds > STALE_STARTUP_CALL_SECONDS:
            return False
    return True


def mark_mapping_busy(mapping_name: str, call_log: str) -> None:
    frappe.db.set_value(
        "Vobiz User Mapping",
        mapping_name,
        {
            "availability_status": "Busy",
            "accept_calls": 0,
            "current_call_log": call_log,
            "last_status_at": frappe.utils.now(),
        },
        update_modified=True,
    )


def restore_mapping_after_call(call_log: str) -> None:
    if not frappe.db.exists("Vobiz Call Log", call_log):
        return

    user = frappe.db.get_value("Vobiz Call Log", call_log, "user")
    if not user:
        return

    mapping = get_user_mapping(user)
    if not mapping:
        return

    current_call_log = mapping.get("current_call_log")
    if current_call_log and current_call_log != call_log:
        return

    auto_available = frappe.utils.cint(mapping.get("auto_available_after_call"))
    frappe.db.set_value(
        "Vobiz User Mapping",
        mapping["name"],
        {
            "availability_status": "Available" if auto_available else "Away",
            "accept_calls": 1 if auto_available else 0,
            "current_call_log": "",
            "last_status_at": frappe.utils.now(),
        },
        update_modified=True,
    )


def create_call_log(
    *,
    reference_doctype: str,
    reference_name: str,
    phone_field: str | None,
    customer_number: str,
    user_mobile: str,
    caller_id: str,
    call_flow: str = "Customer First",
):
    call_log = create_outbound_call_log(
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        phone_field=phone_field,
        customer_number=customer_number,
        user_mobile=user_mobile,
        caller_id=caller_id,
        call_flow=call_flow,
        user=frappe.session.user,
    )
    return call_log


def resolve_target_number(doc, phone_field: str | None = None, phone_number: str | None = None) -> tuple[str, str | None]:
    if phone_field:
        df = doc.meta.get_field(phone_field)
        if not df or not is_phone_like_field(df):
            frappe.throw(_("Field {0} is not a callable phone field.").format(phone_field))

        value = doc.get(phone_field)
        if not value:
            frappe.throw(_("No phone number found in {0}.").format(phone_field))

        if phone_number and not numbers_match(value, phone_number):
            frappe.throw(_("Selected phone number does not match the document field."))

        return value, phone_field

    if phone_number:
        for fieldname, value in collect_phone_candidates(doc) + collect_linked_customer_phone_candidates(doc):
            if numbers_match(value, phone_number):
                return phone_number, fieldname
        frappe.throw(_("Selected phone number was not found on this document."))

    for fieldname in PREFERRED_PHONE_FIELDS:
        df = doc.meta.get_field(fieldname)
        if df and doc.get(fieldname):
            return doc.get(fieldname), fieldname

    for fieldname, value in collect_phone_candidates(doc):
        if value:
            return value, fieldname

    for fieldname, value in collect_linked_customer_phone_candidates(doc):
        if value:
            return value, fieldname

    frappe.throw(_("No callable phone number found on this document."))


def collect_phone_candidates(doc) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    for df in doc.meta.fields:
        if df.fieldtype == "Table":
            for row in doc.get(df.fieldname) or []:
                for child_df in row.meta.fields:
                    if is_phone_like_field(child_df):
                        value = row.get(child_df.fieldname)
                        if value:
                            candidates.append((f"{df.fieldname}.{row.idx}.{child_df.fieldname}", value))
            continue

        if is_phone_like_field(df):
            value = doc.get(df.fieldname)
            if value:
                candidates.append((df.fieldname, value))

    return candidates


def collect_linked_customer_phone_candidates(doc) -> list[tuple[str, str]]:
    customer = doc.get("customer") if doc.meta.has_field("customer") else ""
    if not customer or not frappe.db.exists("DocType", "Customer") or not frappe.db.exists("Customer", customer):
        return []
    customer_doc = frappe.get_doc("Customer", customer)
    candidates = []
    for fieldname in PREFERRED_PHONE_FIELDS:
        df = customer_doc.meta.get_field(fieldname)
        if df and customer_doc.get(fieldname):
            candidates.append((f"customer.{fieldname}", customer_doc.get(fieldname)))
    for fieldname, value in collect_phone_candidates(customer_doc):
        key = f"customer.{fieldname}"
        if value and all(existing_key != key for existing_key, _existing_value in candidates):
            candidates.append((key, value))
    for fieldname, value in collect_linked_customer_contact_phone_candidates(customer):
        key = f"customer.contact.{fieldname}"
        if value and all(existing_key != key for existing_key, _existing_value in candidates):
            candidates.append((key, value))
    return candidates


def collect_linked_customer_contact_phone_candidates(customer: str) -> list[tuple[str, str]]:
    if not frappe.db.exists("DocType", "Contact") or not frappe.db.exists("DocType", "Dynamic Link"):
        return []
    rows = frappe.get_all(
        "Dynamic Link",
        filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Contact"},
        fields=["parent"],
        order_by="modified desc",
        limit_page_length=20,
    )
    candidates = []
    for row in rows:
        contact = frappe.get_doc("Contact", row.parent)
        for fieldname, value in collect_phone_candidates(contact):
            if value and all(existing_key != fieldname for existing_key, _existing_value in candidates):
                candidates.append((fieldname, value))
    return candidates


def is_phone_like_field(df) -> bool:
    fieldname = (df.fieldname or "").lower()
    label = (df.label or "").lower()
    options = (df.options or "").lower()
    return (
        df.fieldtype in ("Data", "Phone", "Small Text")
        and (options == "phone" or any(hint in fieldname or hint in label for hint in PHONE_FIELD_HINTS))
    )


def redact_callback_tokens(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = {}
    for key, value in payload.items():
        if isinstance(value, str) and "token=" in value:
            redacted[key] = value.split("token=", 1)[0] + "token=***"
        else:
            redacted[key] = value
    return redacted


def merge_json(existing: str | None, patch: dict[str, Any]) -> str:
    data = {}
    if existing:
        try:
            data = json.loads(existing)
        except Exception:
            data = {"raw": existing}
    data.update(patch)
    return json.dumps(data, indent=2, default=str)
