from __future__ import annotations

import json
from typing import Any
from xml.sax.saxutils import quoteattr

import frappe

from vobiz_click_to_call.services.client import VobizClient, extract_provider_id
from vobiz_click_to_call.services.settings import build_callback_url, get_settings


def uses_session_recording(doc) -> bool:
    try:
        request = json.loads(doc.get("recording_request_json") or "{}")
    except (ValueError, TypeError):
        return False
    return isinstance(request, dict) and request.get("mode") == "provider_session"


def has_session_recording(call_log: str) -> bool:
    return uses_session_recording({"recording_request_json": frappe.db.get_value(
        "Vobiz Call Log", call_log, "recording_request_json")})


def session_recording_xml(doc, settings) -> str:
    """Arm recording in the answer XML, before Dial, without a worker/API round trip.

    Starting means instructions were prepared, not proof audio was recorded.
    The normal authenticated recording callback remains the completion authority.
    Repeat answer requests get the same instruction; legacy REST jobs cannot
    start another recorder, even after the call ends or settings change.
    """
    if not doc or not frappe.utils.cint(settings.get("enable_recording")):
        return ""
    previous = frappe.db.get_value("Vobiz Call Log", doc.name,
        ["recording_request_json", "recording_status", "recording_id"], as_dict=True) or {}
    if previous.get("recording_id") or previous.get("recording_status") in {"Started", "Completed"}:
        return ""
    if previous.get("recording_status") == "Starting" and not uses_session_recording(previous):
        return ""  # A legacy REST start is already in progress.
    limit = max(1, int(settings.get("recording_time_limit") or settings.get("max_call_duration") or 3600))
    attrs = {
        "recordSession": "true", "redirect": "false", "playBeep": "false",
        "fileFormat": settings.get("recording_format") or "mp3",
        "maxLength": str(limit), "timeout": str(limit),
        "callbackUrl": build_callback_url("vobiz_click_to_call.api.webhook.recording_callback",
            doc.name, doc.callback_token, settings),
        "callbackMethod": "POST",
    }
    if frappe.utils.cint(settings.get("enable_transcription")):
        attrs.update(transcriptionType=settings.get("transcription_type") or "auto",
            transcriptionUrl=build_callback_url("vobiz_click_to_call.api.webhook.transcription_callback",
                doc.name, doc.callback_token, settings), transcriptionMethod="POST")
    if not uses_session_recording(previous):
        values = {
            "recording_request_json": json.dumps({"mode": "provider_session",
                "attributes": redact_callback_tokens(attrs)}, default=str),
            "recording_status": "Starting",
            "recording_error": "",
            "transcript_status": "Requested" if settings.get("enable_transcription") else "Not Requested",
        }
        if doc.get("call_uuid"):
            values["recording_call_uuid"] = doc.call_uuid
        frappe.db.set_value("Vobiz Call Log", doc.name, values, update_modified=False)
        # Keep in-memory documents consistent if callers save after building XML.
        doc.update(values)
    return "<Record " + " ".join(f"{key}={quoteattr(value)}" for key, value in attrs.items()) + "/>"


def start_recording_if_needed(call_log: str) -> None:
    settings = get_settings()
    if not settings.enable_recording:
        return

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if uses_session_recording(doc):
        return
    if doc.recording_status in {"Starting", "Started", "Completed"} or doc.recording_id:
        return

    call_uuid = doc.recording_call_uuid or doc.call_uuid or doc.a_leg_uuid
    if not call_uuid:
        frappe.db.set_value(
            "Vobiz Call Log",
            call_log,
            {
                "recording_status": "Failed",
                "recording_error": "Could not start recording because Vobiz Call UUID is missing.",
            },
            update_modified=False,
        )
        frappe.db.commit()
        return

    token = doc.callback_token
    payload = build_recording_payload(doc.name, token, settings)
    frappe.db.set_value(
        "Vobiz Call Log",
        call_log,
        {
            "recording_call_uuid": call_uuid,
            "recording_status": "Starting",
            "recording_started_at": frappe.utils.now(),
            "recording_request_json": json.dumps(redact_callback_tokens(payload), indent=2, default=str),
            "recording_error": "",
            "transcript_status": "Requested" if settings.enable_transcription else "Not Requested",
        },
        update_modified=False,
    )
    frappe.db.commit()

    try:
        response = VobizClient(settings).start_call_recording(call_uuid, payload)
    except Exception as exc:
        current_status = frappe.db.get_value("Vobiz Call Log", call_log, "recording_status")
        if current_status != "Completed":
            frappe.db.set_value(
                "Vobiz Call Log",
                call_log,
                {"recording_status": "Failed", "recording_error": str(exc)},
                update_modified=False,
            )
        frappe.db.commit()
        return

    current = frappe.db.get_value(
        "Vobiz Call Log",
        call_log,
        ["recording_status", "recording_id", "recording_url"],
        as_dict=True,
    ) or {}
    frappe.db.set_value(
        "Vobiz Call Log",
        call_log,
        {
            "recording_status": "Completed" if current.get("recording_status") == "Completed" else "Started",
            "recording_id": extract_provider_id(response, "recording_id", "RecordingID", "id") or current.get("recording_id"),
            "recording_url": extract_provider_id(response, "url", "record_url", "recording_url") or current.get("recording_url"),
            "recording_response_json": json.dumps(response, indent=2, default=str),
            "recording_error": "",
        },
        update_modified=False,
    )
    frappe.db.commit()


def build_recording_payload(call_log: str, token: str, settings) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "time_limit": int(settings.recording_time_limit or settings.max_call_duration or 3600),
        "file_format": settings.recording_format or "mp3",
        "record_channel_type": settings.record_channel_type or "stereo",
        "callback_url": build_callback_url(
            "vobiz_click_to_call.api.webhook.recording_callback",
            call_log,
            token,
            settings,
        ),
        "callback_method": "POST",
    }

    if settings.enable_transcription:
        payload.update(
            {
                "transcription_type": settings.transcription_type or "auto",
                "transcription_url": build_callback_url(
                    "vobiz_click_to_call.api.webhook.transcription_callback",
                    call_log,
                    token,
                    settings,
                ),
            }
        )

    return payload


def redact_callback_tokens(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = {}
    for key, value in payload.items():
        if isinstance(value, str) and "token=" in value:
            redacted[key] = value.split("token=", 1)[0] + "token=***"
        else:
            redacted[key] = value
    return redacted
