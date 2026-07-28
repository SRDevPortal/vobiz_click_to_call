from __future__ import annotations

from urllib.parse import urlparse

import requests

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from werkzeug.wrappers import Response

from vobiz_click_to_call.services.settings import get_auth_credentials, get_settings

MAX_RECORDING_BYTES = 100 * 1024 * 1024


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def download(call_log: str):
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if not _can_access_recording(doc):
        frappe.throw(_("Not permitted."))
    if not doc.recording_url:
        frappe.throw(_("Recording URL is not available yet."))
    if not _is_allowed_recording_url(doc.recording_url):
        frappe.throw(_("Recording URL is not trusted."))

    settings = get_settings()
    auth_id, auth_token = get_auth_credentials(settings)
    if not auth_id or not auth_token:
        frappe.throw(_("Vobiz Auth ID/Auth Token are not configured."))

    response = requests.get(
        doc.recording_url,
        headers={
            "X-Auth-ID": auth_id,
            "X-Auth-Token": auth_token,
        },
        timeout=int(settings.http_timeout or 20),
        stream=True,
    )
    if response.status_code >= 400:
        reason = response.reason or str(response.status_code)
        response.close()
        frappe.throw(_("Unable to fetch Vobiz recording: {0}").format(reason))

    content_length = frappe.utils.cint(response.headers.get("Content-Length"))
    if content_length and content_length > MAX_RECORDING_BYTES:
        response.close()
        frappe.throw(_("Recording is too large to proxy through the application server."))

    extension = _extension_from_url(doc.recording_url)

    def generate():
        received = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > MAX_RECORDING_BYTES:
                    raise RuntimeError("Recording exceeded the application proxy size limit.")
                yield chunk
        finally:
            response.close()

    return Response(
        generate(),
        headers={
            "Content-Disposition": f'attachment; filename="{doc.name}{extension}"',
            "Cache-Control": "private, no-store",
        },
        content_type=response.headers.get("Content-Type") or _content_type(extension),
        direct_passthrough=True,
    )


@frappe.whitelist()
@rate_limit(limit=120, seconds=60)
def stream(call_log: str):
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    doc = frappe.get_doc("Vobiz Call Log", call_log)
    if not _can_access_recording(doc):
        frappe.throw(_("Not permitted."))
    if not doc.recording_url:
        frappe.throw(_("Recording URL is not available yet."))
    if not _is_allowed_recording_url(doc.recording_url):
        frappe.throw(_("Recording URL is not trusted."))

    settings = get_settings()
    auth_id, auth_token = get_auth_credentials(settings)
    if not auth_id or not auth_token:
        frappe.throw(_("Vobiz Auth ID/Auth Token are not configured."))

    headers = {
        "X-Auth-ID": auth_id,
        "X-Auth-Token": auth_token,
    }
    range_header = (getattr(frappe.request, "headers", {}) or {}).get("Range")
    if range_header:
        headers["Range"] = range_header

    response = requests.get(
        doc.recording_url,
        headers=headers,
        timeout=int(settings.http_timeout or 20),
        stream=True,
    )
    if response.status_code >= 400:
        reason = response.reason or str(response.status_code)
        response.close()
        frappe.throw(_("Unable to fetch Vobiz recording: {0}").format(reason))
    content_length = frappe.utils.cint(response.headers.get("Content-Length"))
    if content_length and content_length > MAX_RECORDING_BYTES:
        response.close()
        frappe.throw(_("Recording is too large to proxy through the application server."))

    extension = _extension_from_url(doc.recording_url)
    response_headers = {
        "Accept-Ranges": response.headers.get("Accept-Ranges", "bytes"),
        "Cache-Control": "private, max-age=300",
        "Content-Disposition": f'inline; filename="{doc.name}{extension}"',
    }
    for header in ("Content-Range", "Content-Length"):
        if response.headers.get(header):
            response_headers[header] = response.headers[header]

    def generate():
        received = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    received += len(chunk)
                    if received > MAX_RECORDING_BYTES:
                        raise RuntimeError("Recording exceeded the application proxy size limit.")
                    yield chunk
        finally:
            response.close()

    return Response(
        generate(),
        status=response.status_code,
        headers=response_headers,
        content_type=response.headers.get("Content-Type") or _content_type(extension),
        direct_passthrough=True,
    )


def recording_proxy_url(call_log: str) -> str:
    return f"/api/method/vobiz_click_to_call.api.recording.stream?call_log={frappe.utils.quote(call_log)}"


def recording_download_url(call_log: str) -> str:
    return f"/api/method/vobiz_click_to_call.api.recording.download?call_log={frappe.utils.quote(call_log)}"


def _can_access_recording(doc) -> bool:
    if "System Manager" in frappe.get_roles():
        return True

    user = frappe.session.user
    for fieldname in ("user", "owner", "linked_owner"):
        if doc.get(fieldname) == user:
            return True

    if frappe.has_permission(doc=doc, ptype="read"):
        return True

    for doctype, fieldname in (("CRM Lead", "crm_lead"), ("Patient", "patient")):
        linked_name = doc.get(fieldname)
        if linked_name and frappe.has_permission(doctype, "read", doc=linked_name):
            return True

    return False


def _is_allowed_recording_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "vobiz.ai" or host.endswith(".vobiz.ai"))


def _extension_from_url(url: str) -> str:
    path = urlparse(url or "").path.lower()
    for extension in (".mp3", ".wav", ".m4a", ".ogg"):
        if path.endswith(extension):
            return extension
    return ".mp3"


def _content_type(extension: str) -> str:
    return {
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
    }.get(extension, "audio/mpeg")
