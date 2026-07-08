from __future__ import annotations

import json
from typing import Any

import frappe

from vobiz_click_to_call.services.client import VobizClient, extract_provider_id
from vobiz_click_to_call.services.settings import build_callback_url, get_settings


def start_recording_if_needed(call_log: str) -> None:
    settings = get_settings()
    if not settings.enable_recording:
        return

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if doc.recording_status in {"Starting", "Started", "Completed"} or doc.recording_id:
        return

    call_uuid = doc.recording_call_uuid or doc.call_uuid or doc.a_leg_uuid
    if not call_uuid:
        doc.recording_status = "Failed"
        doc.recording_error = "Could not start recording because Vobiz Call UUID is missing."
        doc.reload()
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return

    token = doc.callback_token
    payload = build_recording_payload(doc.name, token, settings)
    doc.recording_call_uuid = call_uuid
    doc.recording_status = "Starting"
    doc.recording_started_at = frappe.utils.now()
    doc.recording_request_json = json.dumps(redact_callback_tokens(payload), indent=2, default=str)
    doc.transcript_status = "Requested" if settings.enable_transcription else "Not Requested"
    doc.reload()
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    try:
        response = VobizClient(settings).start_call_recording(call_uuid, payload)
    except Exception as exc:
        doc = frappe.get_doc("Vobiz Call Log", call_log)
        doc.recording_status = "Failed"
        doc.recording_error = str(exc)
        doc.reload()
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    doc.recording_status = "Started"
    doc.recording_id = extract_provider_id(response, "recording_id", "RecordingID", "id") or doc.recording_id
    doc.recording_url = extract_provider_id(response, "url", "record_url", "recording_url") or doc.recording_url
    doc.recording_response_json = json.dumps(response, indent=2, default=str)
    doc.reload()
    doc.save(ignore_permissions=True)
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
