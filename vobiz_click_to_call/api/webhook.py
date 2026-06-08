from __future__ import annotations

import json
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

import frappe

from vobiz_ai.api.call_log import append_callback, sync_linked_summaries
from vobiz_click_to_call.api.call import restore_mapping_after_call
from vobiz_click_to_call.services.ai import enqueue_ai_disposition
from vobiz_click_to_call.services.debug_log import log_vobiz_event
from vobiz_click_to_call.services.disposition import update_reference_call_metrics
from vobiz_click_to_call.services.settings import build_callback_url, get_settings


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def answer(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        log_vobiz_event("Answer callback ignored: invalid call log or token", severity="Warning", payload=payload)
        return _xml_response(_hangup_xml())

    _log_webhook_event("answer received", doc, payload)
    _apply_common_payload(doc, payload)
    doc.status = "Agent Answered" if doc.call_flow == "Agent First" else "Customer Answered"
    if not doc.answer_time:
        doc.answer_time = frappe.utils.now()
    doc.save(ignore_permissions=True)
    _append_callback_if_enabled(doc.name, "answer", payload)
    frappe.db.commit()

    if not doc.user_mobile:
        doc.status = "Failed"
        doc.error_message = "Mapped user mobile number is missing."
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        _log_webhook_event("answer failed: mapped user mobile missing", doc, payload, severity="Error")
        return _xml_response(_hangup_xml())

    xml = _dial_xml(doc)
    _log_webhook_event("answer returning dial xml", doc, {"xml": xml})
    return _xml_response(xml)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def ring(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        log_vobiz_event("Ring callback ignored: invalid call log or token", severity="Warning", payload=payload)
        return _plain_response("IGNORED")

    _log_webhook_event("ring received", doc, payload)
    _apply_common_payload(doc, payload)
    if doc.status == "Queued":
        doc.status = "Ringing"
    if not doc.start_time:
        doc.start_time = frappe.utils.now()
    doc.save(ignore_permissions=True)
    _append_callback_if_enabled(doc.name, "ring", payload)
    frappe.db.commit()
    return _plain_response("OK")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def dial_callback(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        log_vobiz_event("Dial callback ignored: invalid call log or token", severity="Warning", payload=payload)
        return _plain_response("IGNORED")

    _log_webhook_event("dial_callback received", doc, payload)
    _apply_common_payload(doc, payload)
    dial_status = _first_value(
        payload,
        "DialCallStatus",
        "dial_call_status",
        "DialStatus",
        "dial_status",
        "DialAction",
        "DialBLegStatus",
    )
    if dial_status:
        doc.dial_status = dial_status
        doc.status = _status_from_dial_status(dial_status, previous=doc.status)

    if doc.status == "Connected" and not doc.answer_time:
        doc.answer_time = frappe.utils.now()

    doc.save(ignore_permissions=True)
    _append_callback_if_enabled(doc.name, "dial_callback", payload)
    frappe.db.commit()
    if doc.status == "Connected":
        _start_recording_safely(doc.name)
    return _plain_response("OK")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def dial_action(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        log_vobiz_event("Dial action ignored: invalid call log or token", severity="Warning", payload=payload)
        return _xml_response(_hangup_xml())

    _log_webhook_event("dial_action received", doc, payload)
    _apply_common_payload(doc, payload)
    dial_status = _first_value(
        payload,
        "DialCallStatus",
        "dial_call_status",
        "DialStatus",
        "dial_status",
        "DialAction",
        "DialBLegStatus",
    )
    if dial_status:
        doc.dial_status = dial_status
        doc.status = _status_from_dial_status(dial_status, previous=doc.status)
        if str(dial_status).strip().lower().replace("_", "-") == "completed":
            doc.status = "Completed"
            if not doc.end_time:
                doc.end_time = frappe.utils.now()

    doc.save(ignore_permissions=True)
    _append_callback_if_enabled(doc.name, "dial_action", payload)
    if doc.status in {"Completed", "Failed", "Busy", "No Answer", "Cancelled"}:
        update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
        sync_linked_summaries(doc)
        _enqueue_ai_if_ready(doc)
    frappe.db.commit()
    if doc.status == "Connected":
        _start_recording_safely(doc.name)
    elif doc.status in {"Completed", "Failed", "Busy", "No Answer", "Cancelled"}:
        restore_mapping_after_call(doc.name)
        frappe.db.commit()
    xml = _wait_xml()
    _log_webhook_event("dial_action returning wait xml", doc, {"xml": xml, "status": doc.status})
    return _xml_response(xml)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def hangup(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        log_vobiz_event("Hangup callback ignored: invalid call log or token", severity="Warning", payload=payload)
        return _plain_response("IGNORED")

    _log_webhook_event("hangup received", doc, payload)
    _apply_common_payload(doc, payload)
    status = _first_value(payload, "CallStatus", "call_status", "Status", "status")
    hangup_cause = _first_value(payload, "HangupCause", "hangup_cause", "Cause", "cause")

    if status:
        doc.call_status = status
    if hangup_cause:
        doc.hangup_cause = hangup_cause

    doc.status = _status_from_hangup(status, hangup_cause, previous=doc.status)
    if not doc.end_time:
        doc.end_time = frappe.utils.now()

    doc.save(ignore_permissions=True)
    _append_callback_if_enabled(doc.name, "hangup", payload)
    restore_mapping_after_call(doc.name)
    update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
    sync_linked_summaries(doc)
    _enqueue_ai_if_ready(doc)
    frappe.db.commit()
    return _plain_response("OK")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def fallback(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        log_vobiz_event("Fallback callback ignored: invalid call log or token", severity="Warning", payload=payload)
        return _plain_response("IGNORED")

    _log_webhook_event("fallback received", doc, payload, severity="Error")
    _apply_common_payload(doc, payload)
    doc.status = "Failed"
    doc.error_message = _first_value(payload, "error", "Error", "message", "Message") or "Vobiz fallback callback received."
    if not doc.end_time:
        doc.end_time = frappe.utils.now()
    doc.save(ignore_permissions=True)
    _append_callback_if_enabled(doc.name, "fallback", payload)
    restore_mapping_after_call(doc.name)
    update_reference_call_metrics(doc.reference_doctype, doc.reference_name)
    sync_linked_summaries(doc)
    _enqueue_ai_if_ready(doc)
    frappe.db.commit()
    return _plain_response("OK")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def recording_callback(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        return _plain_response("IGNORED")

    data = _with_nested_response(payload)
    _apply_common_payload(doc, data)
    doc.recording_id = doc.recording_id or _first_value(data, "recording_id", "RecordingID", "id")
    doc.recording_url = _first_value(data, "record_url", "RecordUrl", "RecordingUrl", "RecordFile", "url") or doc.recording_url
    doc.recording_duration = _safe_int(
        _first_value(data, "recording_duration", "RecordingDuration", "recording_duration_ms", "RecordingDurationMs", "duration")
    )
    doc.recording_started_at = doc.recording_started_at or _timestamp_or_now(
        _first_value(data, "recording_start_ms", "RecordingStartMs")
    )
    doc.recording_completed_at = _timestamp_or_now(
        _first_value(data, "recording_end_ms", "RecordingEndMs")
    )
    doc.recording_status = "Completed"
    doc.recording_response_json = _json_dumps(data)
    doc.save(ignore_permissions=True)
    _append_callback_if_enabled(doc.name, "recording_callback", payload)
    frappe.db.commit()
    return _plain_response("OK")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def transcription_callback(call_log: str | None = None, token: str | None = None):
    doc, payload = _validate_callback(call_log, token)
    if not doc:
        return _plain_response("IGNORED")

    data = _with_nested_response(payload)
    _apply_transcription_payload(doc, data, payload)
    return _plain_response("OK")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def transcription_event():
    payload = _payload()
    data = _with_nested_response(payload)
    doc = _find_call_log_from_provider_payload(data)
    if not doc:
        log_vobiz_event("Transcription event ignored: matching call log not found", severity="Warning", payload=data)
        return _plain_response("IGNORED")

    _log_webhook_event("transcription_event received", doc, data)
    _apply_transcription_payload(doc, data, payload)
    return _plain_response("OK")


def _apply_transcription_payload(doc, data: dict, original_payload: dict | None = None) -> None:
    error = _first_value(data, "error", "Error")
    transcript = _first_value(data, "transcription", "transcript", "text", "transcription_text", "Transcript")
    _apply_common_payload(doc, data)
    doc.recording_id = doc.recording_id or _first_value(data, "recording_id", "RecordingID")
    doc.transcription_id = doc.transcription_id or _first_value(data, "transcription_id", "TranscriptionID", "id")
    doc.transcript_received_at = frappe.utils.now()
    doc.transcript_json = _json_dumps(data)

    if error:
        doc.transcript_status = "Failed"
        doc.transcript_error = error
    elif transcript:
        doc.transcript_status = "Completed"
        doc.transcript_text = transcript
        doc.ai_disposition_status = "Queued" if get_settings().enable_ai_disposition else "Not Requested"
    else:
        doc.transcript_status = "Failed"
        doc.transcript_error = "Vobiz transcription callback did not include transcript text."

    doc.save(ignore_permissions=True)
    _append_callback_if_enabled(doc.name, "transcription_callback", original_payload or data)
    frappe.db.commit()

    if doc.transcript_status == "Completed":
        enqueue_ai_disposition(doc.name)


def _enqueue_ai_if_ready(doc) -> None:
    if not get_settings().enable_ai_disposition:
        return
    if doc.transcript_status == "Completed" and doc.transcript_text:
        enqueue_ai_disposition(doc.name)


def _find_call_log_from_provider_payload(payload: dict):
    if not frappe.db.exists("DocType", "Vobiz Call Log"):
        return None

    candidates = [
        ("call_uuid", _first_value(payload, "call_uuid", "CallUUID", "uuid")),
        ("request_uuid", _first_value(payload, "request_uuid", "requestUUID", "request_id", "requestId")),
        ("recording_id", _first_value(payload, "recording_id", "RecordingID")),
        ("transcription_id", _first_value(payload, "transcription_id", "TranscriptionID")),
    ]
    for fieldname, value in candidates:
        if not value or not frappe.get_meta("Vobiz Call Log").has_field(fieldname):
            continue
        call_log = frappe.db.get_value("Vobiz Call Log", {fieldname: value}, "name", order_by="creation desc")
        if call_log:
            return frappe.get_doc("Vobiz Call Log", call_log)
    return None


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


def _apply_common_payload(doc, payload: dict) -> None:
    doc.call_uuid = doc.call_uuid or _first_value(payload, "CallUUID", "call_uuid", "uuid")
    doc.a_leg_uuid = doc.a_leg_uuid or _first_value(
        payload,
        "ALegUUID",
        "a_leg_uuid",
        "ParentCallUUID",
        "DialALegUUID",
        "dial_a_leg_uuid",
    )
    doc.b_leg_uuid = doc.b_leg_uuid or _first_value(
        payload,
        "BLegUUID",
        "b_leg_uuid",
        "DialBLegUUID",
        "DialCallUUID",
        "dial_call_uuid",
    )

    doc.start_time = doc.start_time or _first_value(payload, "StartTime", "start_time", "Start")
    doc.answer_time = doc.answer_time or _first_value(payload, "AnswerTime", "answer_time", "Answer")
    doc.end_time = doc.end_time or _first_value(payload, "EndTime", "end_time", "End")

    duration = _first_value(payload, "Duration", "duration", "DialBLegDuration", "dial_b_leg_duration")
    billsec = _first_value(
        payload,
        "BillSec",
        "billsec",
        "BillSeconds",
        "bill_seconds",
        "BillDuration",
        "bill_duration",
        "DialBLegBillDuration",
        "dial_b_leg_bill_duration",
    )
    cost = _first_value(payload, "Cost", "cost", "TotalCost", "total_cost", "DialBLegTotalCost")

    if duration and not doc.duration:
        doc.duration = _safe_int(duration)
    if billsec and not doc.billsec:
        doc.billsec = _safe_int(billsec)
    if cost and not doc.cost:
        doc.cost = _safe_float(cost)


def _with_nested_response(payload: dict) -> dict:
    data = dict(payload or {})
    nested = data.get("response")
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except Exception:
            nested = None
    if isinstance(nested, dict):
        data.update(nested)
    return data


def _dial_xml(doc) -> str:
    settings = get_settings()
    callback_url = build_callback_url(
        "vobiz_click_to_call.api.webhook.dial_callback",
        doc.name,
        doc.callback_token,
        settings,
    )
    action_url = build_callback_url(
        "vobiz_click_to_call.api.webhook.dial_action",
        doc.name,
        doc.callback_token,
        settings,
    )

    attrs = [
        ("action", action_url),
        ("method", "POST"),
        ("callbackUrl", callback_url),
        ("callbackMethod", "POST"),
    ]
    if doc.caller_id:
        attrs.append(("callerId", doc.caller_id))
    if settings.agent_ring_timeout:
        attrs.append(("timeout", str(int(settings.agent_ring_timeout))))
    if settings.max_call_duration:
        attrs.append(("timeLimit", str(int(settings.max_call_duration))))

    attr_text = " ".join(f"{name}={quoteattr(value)}" for name, value in attrs)
    dial_number = doc.customer_number if doc.call_flow == "Agent First" else doc.user_mobile
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        f"<Dial {attr_text}>"
        f"<Number>{escape(dial_number)}</Number>"
        "</Dial>"
        "</Response>"
    )


def _hangup_xml() -> str:
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Hangup /></Response>"


def _wait_xml() -> str:
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Wait length=\"1\" /></Response>"


def _xml_response(xml: str):
    return _raw_response(xml, "text/xml", "vobiz.xml")


def _plain_response(text: str):
    return _raw_response(text, "text/plain", "vobiz.txt")


def _raw_response(content: str, content_type: str, filename: str):
    frappe.local.response["type"] = "download"
    frappe.local.response["filename"] = filename
    frappe.local.response["filecontent"] = content
    frappe.local.response["content_type"] = content_type
    frappe.local.response["display_content_as"] = "inline"


def _append_callback_if_enabled(call_log: str, event: str, payload: dict) -> None:
    try:
        if get_settings().store_raw_payloads:
            append_callback(call_log, event, payload)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Vobiz callback payload append failed")


def _log_webhook_event(message: str, doc, payload: dict, severity: str = "Info") -> None:
    log_vobiz_event(
        f"Webhook {message}",
        call_log=getattr(doc, "name", None),
        severity=severity,
        process_type="Webhook",
        payload={
            "call_log": getattr(doc, "name", None),
            "status_before_or_after": getattr(doc, "status", None),
            "call_flow": getattr(doc, "call_flow", None),
            "call_status": getattr(doc, "call_status", None),
            "dial_status": getattr(doc, "dial_status", None),
            "hangup_cause": getattr(doc, "hangup_cause", None),
            "payload": payload,
        },
    )


def _start_recording_safely(call_log: str) -> None:
    try:
        frappe.enqueue(
            "vobiz_click_to_call.services.recording.start_recording_if_needed",
            queue="short",
            timeout=180,
            call_log=call_log,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Vobiz recording start failed")


def _first_value(payload: dict, *keys: str):
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _json_dumps(payload: dict) -> str:
    safe_payload = dict(payload or {})
    safe_payload.pop("token", None)
    safe_payload.pop("cmd", None)
    return json.dumps(safe_payload, indent=2, default=str)


def _timestamp_or_now(value):
    if not value:
        return frappe.utils.now()

    try:
        timestamp = float(value)
        if timestamp > 100000000000:
            timestamp = timestamp / 1000
        return frappe.utils.get_datetime(datetime.fromtimestamp(timestamp))
    except Exception:
        return frappe.utils.now()


def _safe_int(value) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _safe_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _status_from_dial_status(dial_status: str, *, previous: str) -> str:
    normalized = str(dial_status or "").strip().lower().replace("_", "-")
    if normalized in {"answered", "answer", "in-progress", "in progress", "connected"}:
        return "Connected"
    if normalized in {"completed"}:
        return "Completed" if previous == "Connected" else "Completed"
    if normalized in {"ringing", "queued"}:
        return "Agent Ringing"
    if normalized in {"busy"}:
        return "Busy"
    if normalized in {"no-answer", "no answer", "timeout"}:
        return "No Answer"
    if normalized in {"failed", "canceled", "cancelled"}:
        return "Failed"
    if normalized in {"hangup"}:
        return "Completed" if previous == "Connected" else previous or "Completed"
    return previous or "Customer Answered"


def _status_from_hangup(call_status: str | None, hangup_cause: str | None, *, previous: str) -> str:
    status = str(call_status or "").strip().lower().replace("_", "-")
    cause = str(hangup_cause or "").strip().lower().replace("_", "-")
    combined = f"{status} {cause}"

    if "busy" in combined:
        return "Busy"
    if "no-answer" in combined or "no answer" in combined or "timeout" in combined:
        return "No Answer"
    if "cancel" in combined:
        return "Cancelled"
    if "fail" in combined or "error" in combined:
        return "Failed"
    if previous in {"Busy", "No Answer", "Failed", "Cancelled", "Canceled"} and status in {"completed", "hangup"}:
        return previous
    if previous in {"Agent Answered", "Customer Answered", "Agent Ringing"} and status in {"completed", "hangup"}:
        return "Cancelled"
    if previous in {"Queued", "Ringing"} and status in {"completed", "hangup"}:
        return "Cancelled"
    if previous == "Connected":
        return "Completed"
    return "Completed" if status in {"completed", "hangup"} else previous or "Completed"
