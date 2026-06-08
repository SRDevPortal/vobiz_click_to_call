from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

import frappe

from vobiz_click_to_call.services.numbers import normalize_phone_number


def get_settings():
    return frappe.get_single("Vobiz Settings")


def get_auth_credentials(settings=None) -> tuple[str, str]:
    settings = settings or get_settings()
    auth_id = (settings.auth_id or frappe.conf.get("vobiz_auth_id") or "").strip()

    auth_token = ""
    try:
        auth_token = settings.get_password("auth_token") or ""
    except Exception:
        auth_token = ""
    auth_token = (auth_token or frappe.conf.get("vobiz_auth_token") or "").strip()

    return auth_id, auth_token


def get_allowed_doctypes(settings=None) -> set[str]:
    settings = settings or get_settings()
    raw = settings.allowed_doctypes or "CRM Lead\nContact\nPatient\nCustomer"
    return {row.strip() for row in raw.replace(",", "\n").splitlines() if row.strip()}


def get_default_country_code(settings=None) -> str:
    settings = settings or get_settings()
    return (settings.default_country_code or frappe.conf.get("vobiz_default_country_code") or "+91").strip()


def get_caller_id(settings=None, mapping: dict | None = None) -> str:
    settings = settings or get_settings()
    default_country_code = get_default_country_code(settings)
    caller_id = (
        (mapping or {}).get("caller_id")
        or settings.default_caller_id
        or frappe.conf.get("vobiz_default_caller_id")
        or ""
    )
    return normalize_phone_number(caller_id, default_country_code=default_country_code)


def get_openai_api_key(settings=None) -> str:
    settings = settings or get_settings()

    api_key = ""
    try:
        api_key = settings.get_password("openai_api_key") or ""
    except Exception:
        api_key = ""

    return (
        api_key
        or frappe.conf.get("vobiz_openai_api_key")
        or frappe.conf.get("openai_api_key")
        or ""
    ).strip()


def get_disposition_options(settings=None) -> list[str]:
    settings = settings or get_settings()
    raw = settings.ai_disposition_options or "Interested\nNot Interested\nFollow Up\nUnknown"
    return [row.strip() for row in raw.replace(",", "\n").splitlines() if row.strip()]


def get_manual_disposition_options(settings=None) -> list[str]:
    settings = settings or get_settings()
    raw = settings.manual_disposition_options or (
        "Connected\nNo Answer\nBusy\nFailed\nWrong Number\nNot Interested\nInterested\n"
        "Follow Up Required\nConverted\nCall Back Later\nLanguage Issue\nDuplicate Lead\n"
        "Invalid Number\nDo Not Call"
    )
    return [row.strip() for row in raw.replace(",", "\n").splitlines() if row.strip()]


def get_webhook_base_url(settings=None) -> str:
    settings = settings or get_settings()
    base_url = (
        settings.webhook_base_url
        or frappe.conf.get("vobiz_webhook_base_url")
        or frappe.utils.get_url()
        or ""
    )
    return normalize_public_callback_base_url(base_url)


def normalize_public_callback_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""

    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    is_https_tunnel = parsed.scheme == "https" and hostname.endswith(
        (".ngrok-free.dev", ".ngrok-free.app", ".ngrok.io")
    )
    if is_https_tunnel and parsed.port:
        netloc = hostname
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            netloc = f"{userinfo}@{netloc}"
        base_url = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))

    return base_url.rstrip("/")


def build_callback_url(method_path: str, call_log: str, token: str, settings=None) -> str:
    base_url = get_webhook_base_url(settings)
    return f"{base_url}/api/method/{method_path}?call_log={quote(call_log)}&token={quote(token)}"
