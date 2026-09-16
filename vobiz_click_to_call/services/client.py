from __future__ import annotations

from typing import Any
from urllib.parse import quote
from email.utils import parsedate_to_datetime
import time

import requests

import frappe
from frappe import _

from vobiz_click_to_call.services.settings import get_auth_credentials, get_settings


class ProviderTemporaryError(Exception):
    """A read can be retried later; this never means that a call has ended."""

    def __init__(self, message: str, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = max(0, min(float(retry_after or 0), 3600))


def _retry_after(value):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0


class VobizClient:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.auth_id, self.auth_token = get_auth_credentials(self.settings)
        self.base_url = (self.settings.base_url or "https://api.vobiz.ai/api/v1").strip().rstrip("/")
        self.timeout = max(1, int(self.settings.http_timeout or 20))

    def make_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.auth_id or not self.auth_token:
            frappe.throw(_("Vobiz Auth ID/Auth Token are not configured."))

        url = f"{self.base_url}/Account/{self.auth_id}/Call/"
        return self._post(url, payload, "Vobiz call request failed")

    def start_call_recording(self, call_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.auth_id or not self.auth_token:
            frappe.throw(_("Vobiz Auth ID/Auth Token are not configured."))
        if not call_uuid:
            frappe.throw(_("Vobiz Call UUID is required to start recording."))

        url = f"{self.base_url}/Account/{self.auth_id}/Call/{call_uuid}/Record/"
        return self._post(url, payload, "Vobiz recording request failed")

    def hangup_call(self, call_uuid: str, *, allow_missing: bool = False) -> dict[str, Any]:
        if not self.auth_id or not self.auth_token:
            frappe.throw(_("Vobiz Auth ID/Auth Token are not configured."))
        if not call_uuid:
            frappe.throw(_("Vobiz Call UUID is required to cancel a call."))

        url = f"{self.base_url}/Account/{self.auth_id}/Call/{call_uuid}/"
        response = requests.delete(
            url,
            headers={
                "X-Auth-ID": self.auth_id,
                "X-Auth-Token": self.auth_token,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )

        if response.status_code in (200, 202, 204):
            return {"message": "Call hangup requested", "status_code": response.status_code}

        try:
            data = response.json()
        except Exception:
            data = {"raw_response": response.text}
        message = _bounded_error_message(data.get("message") or data.get("error") or response.text or response.reason)
        # Browser SDK hangup can win the race with this REST request. Opt in only
        # where the caller retains the reservation and reconciles final state.
        if allow_missing and response.status_code in (400, 404, 410) and message.strip().lower() == "call not found":
            return {"message": "Call no longer found; confirm final state", "status_code": response.status_code,
                    "call_missing": True}
        frappe.throw(_("Vobiz hangup request failed: {0}").format(message))

    def search_cdrs(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.auth_id or not self.auth_token:
            frappe.throw(_("Vobiz Auth ID/Auth Token are not configured."))

        url = f"{self.base_url}/Account/{self.auth_id}/cdr/search"
        return self._get(url, params or {}, "Vobiz CDR search failed")

    def retrieve_cdr(self, call_id: str) -> dict[str, Any]:
        if not self.auth_id or not self.auth_token:
            frappe.throw(_("Vobiz Auth ID/Auth Token are not configured."))
        if not call_id:
            return {}
        url = f"{self.base_url}/Account/{self.auth_id}/cdr/{quote(call_id, safe='')}"
        return self._get(url, {}, "Vobiz CDR lookup failed", allow_missing=True)

    def retrieve_live_call(self, call_uuid: str, status: str = "live") -> dict[str, Any]:
        if not self.auth_id or not self.auth_token:
            frappe.throw(_("Vobiz Auth ID/Auth Token are not configured."))
        if not call_uuid:
            frappe.throw(_("Vobiz Call UUID is required."))

        url = f"{self.base_url}/Account/{self.auth_id}/Call/{call_uuid}/"
        return self._get(url, {"status": status}, "Vobiz call status request failed")

    def _post(self, url: str, payload: dict[str, Any], failure_label: str) -> dict[str, Any]:
        response = requests.post(
            url,
            json=payload,
            headers={
                "X-Auth-ID": self.auth_id,
                "X-Auth-Token": self.auth_token,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw_response": response.text}

        if response.status_code >= 400:
            message = _bounded_error_message(data.get("message") or data.get("error") or response.text or response.reason)
            frappe.throw(_("{0}: {1}").format(failure_label, message))

        return data

    def _get(self, url: str, params: dict[str, Any], failure_label: str,
             *, allow_missing: bool = False) -> dict[str, Any]:
        from vobiz_click_to_call.services.recovery_policy import claim_provider_read

        claim_provider_read(self.auth_id)
        try:
            response = requests.get(
                url, params=params,
                headers={"X-Auth-ID": self.auth_id, "X-Auth-Token": self.auth_token,
                         "Content-Type": "application/json"},
                timeout=(min(5, self.timeout), self.timeout),
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            raise ProviderTemporaryError(f"{failure_label}: temporary connection/read failure") from None

        if allow_missing and response.status_code == 404:
            return {}
        if response.status_code in (408, 429, 500, 502, 503, 504):
            raise ProviderTemporaryError(
                f"{failure_label}: HTTP {response.status_code}",
                retry_after=_retry_after(response.headers.get("Retry-After")),
            )

        try:
            data = response.json()
        except Exception:
            data = {"raw_response": response.text}

        if response.status_code >= 400:
            message = _bounded_error_message(data.get("message") or data.get("error") or response.text or response.reason)
            frappe.throw(_("{0}: {1}").format(failure_label, message))

        return data


def _bounded_error_message(value: Any, max_chars: int = 2000) -> str:
    text = str(value or "Unknown provider error")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def extract_provider_id(payload: dict[str, Any] | None, *keys: str) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)

    data = payload.get("data")
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if value:
                return str(value)

    return ""
