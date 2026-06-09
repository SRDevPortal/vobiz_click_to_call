from __future__ import annotations

import json
import secrets
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import frappe
from werkzeug.wrappers import Response

from vobiz_ai.api.call_log import make_outbound_call_key, sync_reference_links
from vobiz_click_to_call.api.call import get_user_mapping
from vobiz_click_to_call.services.debug_log import log_vobiz_event
from vobiz_click_to_call.services.numbers import normalize_phone_number, phone_key, provider_phone_number
from vobiz_click_to_call.services.settings import build_callback_url, get_default_country_code, get_settings


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def route():
    """Route inbound DID callbacks to the agent who last called this customer."""
    payload = _payload()
    event = _event_name(payload)

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

    settings = get_settings()
    default_country_code = get_default_country_code(settings)
    customer_number = normalize_phone_number(_first_value(payload, "From", "from", "Caller", "caller_id"), default_country_code=default_country_code)
    did_number = normalize_phone_number(_first_value(payload, "To", "to", "Called", "did", "DID"), default_country_code=default_country_code)

    if not customer_number:
        log_vobiz_event("Inbound route ignored: caller number missing", severity="Warning", payload=payload)
        return _xml_response(_hangup_xml())

    previous = find_last_customer_agent(customer_number)
    if not previous:
        log_vobiz_event("Inbound route ignored: no previous mapped agent found", severity="Warning", payload=payload)
        return _xml_response(_hangup_xml())

    agent_mobile = _active_agent_mobile(previous)
    if not agent_mobile:
        log_vobiz_event(
            "Inbound route ignored: previous agent mapping unavailable",
            severity="Warning",
            payload={"customer_number": customer_number, "previous_call_log": previous.name, "user": previous.user},
        )
        return _xml_response(_hangup_xml())

    call_log = find_existing_inbound_call(payload) or create_inbound_call_log(previous, customer_number, did_number, agent_mobile, payload)
    update_inbound_call_event(call_log, payload, commit=False)
    publish_callback_notification(call_log, previous, customer_number, did_number, agent_mobile)

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

    xml = _dial_agent_xml(call_log, agent_mobile, settings)
    frappe.db.commit()
    return _xml_response(xml)


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


def create_inbound_call_log(previous, customer_number: str, did_number: str, agent_mobile: str, payload: dict[str, Any]):
    doc = frappe.get_doc(
        {
            "doctype": "Vobiz Call Log",
            "call_key": make_outbound_call_key(),
            "source_app": "vobiz_click_to_call",
            "reference_doctype": previous.reference_doctype,
            "reference_name": previous.reference_name,
            "phone_field": previous.phone_field,
            "user": previous.user,
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
        doc.status = "Completed" if status == "completed" else "Failed"
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
        "ring_time": _safe_int(_first_value(payload, "RingTime", "ring_time")),
        "raw_payload": json.dumps(_safe_payload(payload), indent=2, default=str),
    }
    for fieldname, value in values.items():
        if value not in (None, "") and frappe.get_meta("Vobiz Call Log").has_field(fieldname):
            setattr(doc, fieldname, value)


def _active_agent_mobile(previous) -> str:
    mapping = get_user_mapping(previous.user)
    if not mapping:
        return ""
    return normalize_phone_number(mapping.get("agent_mobile") or previous.user_mobile, default_country_code=get_default_country_code())


def _dial_agent_xml(call_log, agent_mobile: str, settings) -> str:
    action_url = build_callback_url(
        "vobiz_click_to_call.api.webhook.dial_action",
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
