from __future__ import annotations

import json
import hmac
import secrets
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import frappe
from werkzeug.wrappers import Response

from vobiz_ai.api.call_log import make_outbound_call_key, sync_reference_links
from vobiz_click_to_call.api.call import get_user_mapping, restore_mapping_after_call
from vobiz_click_to_call.api.console import is_agent_console_online
from vobiz_click_to_call.services.call_status import status_from_provider
from vobiz_click_to_call.services.debug_log import log_vobiz_event
from vobiz_click_to_call.services.numbers import normalize_phone_number, phone_key, provider_phone_number
from vobiz_click_to_call.services.settings import (
    build_callback_url,
    get_caller_ids,
    get_default_country_code,
    get_inbound_callback_token,
    get_settings,
)

TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def route():
    """Route inbound DID callbacks to the agent who last called this customer."""
    payload = _payload()
    event = _event_name(payload)
    settings = get_settings()
    default_country_code = get_default_country_code(settings)
    did_number = normalize_phone_number(_first_value(payload, "To", "to", "Called", "did", "DID"), default_country_code=default_country_code)

    if not _inbound_callback_allowed(payload, settings, did_number):
        log_vobiz_event(
            "Inbound route rejected: callback token or DID validation failed",
            severity="Warning",
            payload={"event": event, "did_number": did_number, "payload": _safe_payload(payload)},
        )
        return _plain_response("IGNORED") if event == "hangup" else _xml_response(_hangup_xml())

    if event == "hangup":
        call_log = find_existing_inbound_call(payload)
        if call_log:
            update_inbound_call_event(call_log, payload)
        else:
            log_vobiz_event("Inbound hangup ignored: matching call log not found", severity="Warning", payload=payload)
        return _plain_response("OK")

    if event and event not in {"callinitiated", "startapp", "ring"}:
        call_log = find_existing_inbound_call(payload)
        if call_log:
            update_inbound_call_event(call_log, payload)
        return _plain_response("OK")

    customer_number = normalize_phone_number(_first_value(payload, "From", "from", "Caller", "caller_id"), default_country_code=default_country_code)

    if not customer_number:
        log_vobiz_event("Inbound route ignored: caller number missing", severity="Warning", payload=payload)
        return _xml_response(_hangup_xml())

    previous = find_last_customer_agent(customer_number)
    if not previous:
        log_vobiz_event("Inbound route ignored: no previous mapped agent found", severity="Warning", payload=payload)
        return _xml_response(_hangup_xml())

    target = resolve_inbound_target(previous, settings)
    agent_mobile = target.get("agent_mobile")
    if not agent_mobile:
        call_log = find_existing_inbound_call(payload) or create_inbound_call_log(previous, customer_number, did_number, "", payload)
        mark_inbound_missed(
            call_log,
            target.get("reason") or "No mapped agent or fallback number was available.",
            payload,
            commit=False,
        )
        log_vobiz_event(
            "Inbound route ignored: previous agent mapping unavailable",
            severity="Warning",
            payload={"customer_number": customer_number, "previous_call_log": previous.name, "user": previous.user, "reason": target.get("reason")},
        )
        frappe.db.commit()
        return _xml_response(_hangup_xml())

    call_log = find_existing_inbound_call(payload) or create_inbound_call_log(
        previous,
        customer_number,
        did_number,
        agent_mobile,
        payload,
        target_user=target.get("user") or previous.user,
        route_type=target.get("route_type") or "last_agent",
    )
    update_inbound_call_event(call_log, payload, commit=False)
    if target.get("is_mapped_agent"):
        _mark_mapping_busy(target.get("user"), call_log.name)

    if _is_trunk_notification(payload):
        log_vobiz_event(
            "Inbound SIP trunk webhook is informational only; configure the DID on a Vobiz XML Application Answer URL to enable agent callback routing.",
            call_log=call_log.name,
            severity="Warning",
            payload={
                "event": _first_value(payload, "Event", "event"),
                "trunk_id": _first_value(payload, "TrunkID", "trunk_id"),
                "sip_call_id": _first_value(payload, "SIPCallID", "sip_call_id"),
                "customer_number": customer_number,
                "did_number": did_number,
                "mapped_agent": agent_mobile,
            },
        )
        frappe.db.commit()
        return _plain_response("OK")

    if target.get("is_mapped_agent"):
        publish_callback_notification(call_log, previous, customer_number, did_number, agent_mobile)
    xml = _dial_agent_xml(call_log, agent_mobile, settings)
    frappe.db.commit()
    return _xml_response(xml)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def dial_action(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        return _xml_response(_hangup_xml())

    apply_provider_payload(doc, payload)
    status = str(_first_value(payload, "DialCallStatus", "dial_call_status", "DialStatus", "dial_status", "Status", "status") or "").strip().lower()
    failed = _dial_failed(status) or _dial_completed_without_bridge(payload, doc.status)
    if failed and _should_try_end_fallback(doc):
        settings = get_settings()
        end_mobile = _end_fallback_mobile(settings)
        restore_mapping_after_call(doc.name)
        _set_request_flag(doc, "end_fallback_attempted", True)
        doc.agent_number = end_mobile
        doc.user_mobile = end_mobile
        doc.status = "Agent Ringing"
        doc.error_message = "Mapped agent did not answer; routing to end fallback mobile."
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return _xml_response(_dial_agent_xml(doc, end_mobile, settings))

    if status in {"answered", "answer", "in-progress", "in progress", "connected"}:
        doc.status = "Connected"
        doc.answer_time = doc.answer_time or frappe.utils.now()
    elif failed:
        doc.status = "No Answer" if status in {"no-answer", "no answer", "timeout", "completed"} else "Failed"
        doc.end_time = doc.end_time or frappe.utils.now()
    elif status == "completed":
        doc.status = "Completed"
        doc.end_time = doc.end_time or frappe.utils.now()
    doc.request_json = json.dumps(_safe_payload(payload), indent=2, default=str)
    doc.save(ignore_permissions=True)
    if doc.status in TERMINAL_STATUSES:
        restore_mapping_after_call(doc.name)
    frappe.db.commit()
    return _xml_response(_wait_xml())


def find_last_customer_agent(customer_number: str):
    normalized = normalize_phone_number(customer_number, default_country_code=get_default_country_code())
    last10 = phone_key(customer_number)
    filters = {
        "source_app": "vobiz_click_to_call",
        "direction": "Outgoing",
        "user": ["is", "set"],
        "user_mobile": ["is", "set"],
    }
    or_filters = []
    if normalized:
        or_filters.append(["normalized_customer_number", "=", normalized])
        or_filters.append(["customer_number", "=", normalized])
    if last10:
        or_filters.append(["customer_number", "like", f"%{last10}%"])

    rows = frappe.get_all(
        "Vobiz Call Log",
        filters=filters,
        or_filters=or_filters or None,
        fields=[
            "name",
            "user",
            "user_mobile",
            "caller_id",
            "reference_doctype",
            "reference_name",
            "phone_field",
            "crm_lead",
            "patient",
        ],
        order_by="creation desc",
        limit=1,
    )
    return rows[0] if rows else None


def create_inbound_call_log(
    previous,
    customer_number: str,
    did_number: str,
    agent_mobile: str,
    payload: dict[str, Any],
    target_user: str | None = None,
    route_type: str = "last_agent",
):
    doc = frappe.get_doc(
        {
            "doctype": "Vobiz Call Log",
            "call_key": make_outbound_call_key(),
            "source_app": "vobiz_click_to_call",
            "reference_doctype": previous.reference_doctype,
            "reference_name": previous.reference_name,
            "phone_field": previous.phone_field,
            "user": target_user or previous.user,
            "user_mobile": agent_mobile,
            "agent_number": agent_mobile,
            "customer_number": customer_number,
            "normalized_customer_number": normalize_phone_number(customer_number, default_country_code=get_default_country_code()),
            "caller_id": did_number or previous.caller_id,
            "did_number": did_number or previous.caller_id,
            "normalized_did": normalize_phone_number(did_number or previous.caller_id, default_country_code=get_default_country_code()),
            "call_flow": "Customer First",
            "direction": "Incoming",
            "status": "Agent Ringing",
            "from_number": customer_number,
            "to_number": did_number,
            "start_time": frappe.utils.now(),
            "callback_token": secrets.token_urlsafe(24),
            "recording_status": "Not Started",
            "transcript_status": "Not Requested",
            "ai_status": "Pending",
            "ai_disposition_status": "Not Requested",
            "cdr_sync_status": "Not Synced",
            "currency": "INR",
            "request_json": json.dumps(_safe_payload(payload), indent=2, default=str),
            "error_message": f"Inbound route: {route_type}",
        }
    )
    apply_provider_payload(doc, payload)
    doc.crm_lead = previous.crm_lead
    doc.patient = previous.patient
    sync_reference_links(doc)
    doc.insert(ignore_permissions=True)
    return doc


def publish_callback_notification(call_log, previous, customer_number: str, did_number: str, agent_mobile: str) -> None:
    if not getattr(call_log, "user", None):
        return
    try:
        frappe.publish_realtime(
            "vobiz_customer_callback",
            {
                "call_log": call_log.name,
                "reference_doctype": call_log.reference_doctype,
                "reference_name": call_log.reference_name,
                "crm_lead": call_log.crm_lead,
                "patient": call_log.patient,
                "customer_number": customer_number,
                "did_number": did_number,
                "agent_mobile": agent_mobile,
                "agent_user": call_log.user,
                "previous_call_log": getattr(previous, "name", None),
            },
            user=call_log.user,
            after_commit=True,
        )
    except Exception:
        log_vobiz_event(
            "Inbound callback notification failed",
            call_log=call_log.name,
            severity="Warning",
            payload={"user": call_log.user, "customer_number": customer_number, "did_number": did_number},
            traceback=frappe.get_traceback(),
        )


def find_existing_inbound_call(payload: dict[str, Any]):
    identifiers = [
        ("sip_call_id", _first_value(payload, "SIPCallID", "sip_call_id")),
        ("call_uuid", _first_value(payload, "CallUUID", "call_uuid", "uuid")),
        ("request_id", _first_value(payload, "RequestID", "request_id")),
        ("request_uuid", _first_value(payload, "RequestUUID", "request_uuid")),
    ]
    for fieldname, value in identifiers:
        if not value or not frappe.get_meta("Vobiz Call Log").has_field(fieldname):
            continue
        name = frappe.db.get_value(
            "Vobiz Call Log",
            {"source_app": "vobiz_click_to_call", "direction": "Incoming", fieldname: value},
            "name",
            order_by="creation desc",
        )
        if name:
            return frappe.get_doc("Vobiz Call Log", name)
    return None


def update_inbound_call_event(doc, payload: dict[str, Any], *, commit: bool = True) -> None:
    apply_provider_payload(doc, payload)
    event = _event_name(payload)
    status = str(_first_value(payload, "Status", "CallStatus", "status", "call_status") or "").strip().lower()
    reason = _first_value(payload, "Reason", "HangupCause", "DialHangupCause", "hangup_cause")

    if event == "hangup":
        doc.status = status_from_provider(
            {
                "status": status,
                "call_status": status,
                "hangup_cause": reason,
                "duration": doc.duration or _safe_int(_first_value(payload, "Duration", "duration")),
                "billsec": doc.billsec or _safe_int(_first_value(payload, "BillSec", "billsec", "BillSeconds", "bill_seconds")),
            },
            previous=doc.status,
        ) or "Failed"
        doc.call_status = status or doc.call_status
        doc.hangup_cause = reason or doc.hangup_cause
        doc.end_time = _first_value(payload, "EndTime", "end_time") or frappe.utils.now()
    elif event in {"callinitiated", "startapp", "ring"}:
        doc.status = "Agent Ringing"
        doc.call_status = status or doc.call_status
        doc.start_time = _first_value(payload, "StartTime", "start_time") or doc.start_time or frappe.utils.now()

    doc.request_json = json.dumps(_safe_payload(payload), indent=2, default=str)
    doc.save(ignore_permissions=True)
    if commit:
        frappe.db.commit()


def mark_inbound_missed(doc, reason: str, payload: dict[str, Any], *, commit: bool = True) -> None:
    apply_provider_payload(doc, payload)
    doc.status = "No Answer"
    doc.call_status = "missed"
    doc.hangup_cause = "AGENT_CONSOLE_OFFLINE"
    doc.error_message = reason
    doc.end_time = frappe.utils.now()
    doc.request_json = json.dumps(_safe_payload(payload), indent=2, default=str)
    doc.save(ignore_permissions=True)
    log_vobiz_event(
        "Inbound callback missed: mapped agent console inactive",
        call_log=doc.name,
        severity="Warning",
        payload={
            "user": doc.user,
            "customer_number": doc.customer_number,
            "did_number": doc.did_number,
            "reason": reason,
        },
    )
    if commit:
        frappe.db.commit()


def apply_provider_payload(doc, payload: dict[str, Any]) -> None:
    values = {
        "event": _first_value(payload, "Event", "event"),
        "call_uuid": _first_value(payload, "CallUUID", "call_uuid", "uuid"),
        "request_id": _first_value(payload, "RequestID", "request_id"),
        "request_uuid": _first_value(payload, "RequestUUID", "request_uuid"),
        "sip_call_id": _first_value(payload, "SIPCallID", "sip_call_id"),
        "account_id": _first_value(payload, "AccountId", "AccountID", "account_id", "ParentAuthID"),
        "trunk_id": _first_value(payload, "TrunkID", "trunk_id"),
        "domain": _first_value(payload, "Domain", "domain"),
        "event_timestamp": _first_value(payload, "Timestamp", "timestamp"),
        "duration": _safe_int(_first_value(payload, "Duration", "duration")),
        "billsec": _safe_int(_first_value(payload, "BillSec", "billsec", "BillSeconds", "bill_seconds")),
        "ring_time": _safe_int(_first_value(payload, "RingTime", "ring_time")),
        "raw_payload": json.dumps(_safe_payload(payload), indent=2, default=str),
    }
    for fieldname, value in values.items():
        if value not in (None, "") and frappe.get_meta("Vobiz Call Log").has_field(fieldname):
            setattr(doc, fieldname, value)


def resolve_inbound_target(previous, settings=None) -> dict[str, Any]:
    settings = settings or get_settings()
    is_patient = previous.reference_doctype == "Patient" or bool(previous.patient)
    mapping = get_user_mapping(previous.user)
    primary_mobile = _mapping_mobile(mapping, previous.user_mobile)

    if not is_patient:
        if mapping and primary_mobile and _mapping_can_receive(previous.user, mapping):
            return {"user": previous.user, "agent_mobile": primary_mobile, "route_type": "last_agent", "is_mapped_agent": True}
        return {"reason": "Mapped agent is not active on Vobiz Agent Console."}

    if mapping and primary_mobile and _mapping_can_receive(previous.user, mapping):
        return {"user": previous.user, "agent_mobile": primary_mobile, "route_type": "patient_last_agent", "is_mapped_agent": True}

    fallback_user = (mapping or {}).get("fallback_user")
    if fallback_user:
        fallback_mapping = get_user_mapping(fallback_user)
        fallback_mobile = _mapping_mobile(fallback_mapping, "")
        if fallback_mapping and fallback_mobile and _mapping_can_receive(fallback_user, fallback_mapping):
            return {
                "user": fallback_user,
                "agent_mobile": fallback_mobile,
                "route_type": "patient_fallback_user",
                "is_mapped_agent": True,
            }

    end_mobile = _end_fallback_mobile(settings)
    if end_mobile:
        return {"user": previous.user, "agent_mobile": end_mobile, "route_type": "end_fallback_mobile", "is_mapped_agent": False}

    return {"reason": "Patient last agent and fallback user were unavailable, and end fallback is disabled."}


def _active_agent_mobile(previous) -> str:
    mapping = get_user_mapping(previous.user)
    if not mapping:
        return ""
    return normalize_phone_number(mapping.get("agent_mobile") or previous.user_mobile, default_country_code=get_default_country_code())


def _mapping_mobile(mapping: dict[str, Any] | None, fallback: str = "") -> str:
    if not mapping:
        return normalize_phone_number(fallback, default_country_code=get_default_country_code())
    return normalize_phone_number(mapping.get("agent_mobile") or fallback, default_country_code=get_default_country_code())


def _mapping_can_receive(user: str, mapping: dict[str, Any]) -> bool:
    if not is_agent_console_online(user):
        return False
    current_call_log = mapping.get("current_call_log")
    if current_call_log and frappe.db.exists("Vobiz Call Log", current_call_log):
        status = frappe.db.get_value("Vobiz Call Log", current_call_log, "status")
        if status not in TERMINAL_STATUSES:
            return False
    return True


def _inbound_callback_allowed(payload: dict[str, Any], settings, did_number: str) -> bool:
    configured_dids = set(get_caller_ids(settings))
    if configured_dids and (not did_number or did_number not in configured_dids):
        return False

    expected_token = get_inbound_callback_token(settings)
    if not expected_token:
        return True

    received_token = _first_value(payload, "token", "Token", "callback_token", "inbound_token", "inbound_callback_token")
    return hmac.compare_digest(str(expected_token), str(received_token or ""))


def _mark_mapping_busy(user: str | None, call_log: str) -> None:
    if not user:
        return
    mapping_name = frappe.db.get_value("Vobiz User Mapping", {"user": user, "enabled": 1}, "name")
    if not mapping_name:
        return
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


def _end_fallback_mobile(settings=None) -> str:
    settings = settings or get_settings()
    if not frappe.utils.cint(getattr(settings, "enable_end_fallback", 0)):
        return ""
    return normalize_phone_number(getattr(settings, "end_fallback_mobile", "") or "", default_country_code=get_default_country_code(settings))


def _dial_agent_xml(call_log, agent_mobile: str, settings) -> str:
    action_url = build_callback_url(
        "vobiz_click_to_call.api.inbound.dial_action",
        call_log.name,
        call_log.callback_token,
        settings,
    )
    attrs = [
        ("action", action_url),
        ("method", "POST"),
        ("redirect", "false"),
    ]
    if call_log.did_number:
        attrs.append(("callerId", provider_phone_number(call_log.did_number)))
    if settings.agent_ring_timeout:
        attrs.append(("timeout", str(int(settings.agent_ring_timeout))))
    if settings.max_call_duration:
        attrs.append(("timeLimit", str(int(settings.max_call_duration))))
    attr_text = " ".join(f"{name}={quoteattr(value)}" for name, value in attrs)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        f"<Dial {attr_text}>"
        f"<Number>{escape(provider_phone_number(agent_mobile))}</Number>"
        "</Dial>"
        "</Response>"
    )


def _validate_callback(call_log: str | None, token: str | None):
    payload = _payload()
    call_log = call_log or payload.get("call_log")
    token = token or payload.get("token")
    if not call_log or not token or not frappe.db.exists("Vobiz Call Log", call_log):
        return None, payload
    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if not doc.callback_token or not secrets_match(doc.callback_token, token):
        return None, payload
    return doc, payload


def secrets_match(expected: str, received: str) -> bool:
    import hmac

    return hmac.compare_digest(str(expected or ""), str(received or ""))


def _dial_failed(status: str) -> bool:
    normalized = str(status or "").strip().lower().replace("_", "-")
    return normalized in {"busy", "no-answer", "no answer", "timeout", "failed", "canceled", "cancelled"}


def _dial_completed_without_bridge(payload: dict, previous_status: str | None) -> bool:
    status = str(_first_value(payload, "DialCallStatus", "dial_call_status", "DialStatus", "dial_status", "Status", "status") or "").strip().lower().replace("_", "-")
    if status != "completed":
        return False
    b_leg = _first_value(payload, "DialBLegUUID", "BLegUUID", "DialCallUUID", "dial_b_leg_uuid")
    dial_action = str(_first_value(payload, "DialAction", "dial_action") or "").strip().lower()
    return not b_leg and dial_action != "connected" and str(previous_status or "").strip() not in {"Connected", "Completed"}


def _should_try_end_fallback(doc) -> bool:
    if doc.status == "Connected":
        return False
    settings = get_settings()
    end_mobile = _end_fallback_mobile(settings)
    if not end_mobile:
        return False
    if _request_flag(doc, "end_fallback_attempted"):
        return False
    current_mobile = normalize_phone_number(doc.get("agent_number") or doc.get("user_mobile"), default_country_code=get_default_country_code(settings))
    return current_mobile != end_mobile


def _request_flag(doc, key: str) -> bool:
    try:
        data = json.loads(doc.request_json or "{}")
        return bool(data.get(key))
    except Exception:
        return False


def _set_request_flag(doc, key: str, value: Any) -> None:
    try:
        data = json.loads(doc.request_json or "{}")
    except Exception:
        data = {}
    data[key] = value
    doc.request_json = json.dumps(data, indent=2, default=str)


def _wait_xml() -> str:
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Wait length=\"1\" /></Response>"


def _payload() -> dict:
    payload = {}
    try:
        if frappe.request and frappe.request.is_json:
            payload.update(frappe.request.get_json(silent=True) or {})
    except Exception:
        pass
    payload.update(dict(frappe.form_dict or {}))
    payload.pop("cmd", None)
    return payload


def _first_value(payload: dict, *keys: str):
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _event_name(payload: dict[str, Any]) -> str:
    return str(_first_value(payload, "Event", "event") or "").strip().lower().replace("_", "")


def _is_trunk_notification(payload: dict[str, Any]) -> bool:
    return bool(_event_name(payload) and _first_value(payload, "TrunkID", "trunk_id"))


def _safe_int(value) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except Exception:
        return 0


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload = dict(payload or {})
    safe_payload.pop("cmd", None)
    return safe_payload


def _hangup_xml() -> str:
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Hangup /></Response>"


def _xml_response(xml: str):
    return Response(xml, content_type="text/xml; charset=utf-8")


def _plain_response(text: str):
    return Response(text, content_type="text/plain; charset=utf-8")
