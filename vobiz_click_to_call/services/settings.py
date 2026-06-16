from __future__ import annotations

import ipaddress
from urllib.parse import quote, urlsplit, urlunsplit

import frappe
from frappe import _

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
        or _first_caller_id(settings)
        or frappe.conf.get("vobiz_default_caller_id")
        or ""
    )
    return normalize_phone_number(caller_id, default_country_code=default_country_code)


def get_caller_ids(settings=None) -> list[str]:
    settings = settings or get_settings()
    default_country_code = get_default_country_code(settings)
    raw_values = []
    raw_values.extend(_split_lines(getattr(settings, "caller_ids", "") or ""))
    raw_values.append(getattr(settings, "default_caller_id", "") or "")
    raw_values.append(frappe.conf.get("vobiz_default_caller_id") or "")

    numbers = []
    seen = set()
    for value in raw_values:
        number = normalize_phone_number(value, default_country_code=default_country_code)
        if number and number not in seen:
            numbers.append(number)
            seen.add(number)
    return numbers


def _first_caller_id(settings=None) -> str:
    ids = get_caller_ids(settings)
    return ids[0] if ids else ""


def _split_lines(value: str) -> list[str]:
    return [row.strip() for row in str(value or "").replace(",", "\n").splitlines() if row.strip()]


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
    try:
        from vobiz_click_to_call.services.lead_disposition import get_lead_disposition_options

        options = get_lead_disposition_options()
        if options:
            return options
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Vobiz SR Lead Disposition options failed")

    settings = settings or get_settings()
    raw = settings.ai_disposition_options or "Interested\nNot Interested\nFollow Up\nUnknown"
    return [row.strip() for row in raw.replace(",", "\n").splitlines() if row.strip()]


def get_manual_disposition_options(
    settings=None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    lead_status: str | None = None,
) -> list[str]:
    try:
        from vobiz_click_to_call.services.lead_disposition import get_lead_disposition_options

        options = get_lead_disposition_options(reference_doctype, reference_name, lead_status)
        if options:
            return options
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Vobiz manual SR Lead Disposition options failed")

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
    base_url = normalize_public_callback_base_url(base_url)
    validate_public_callback_base_url(base_url)
    return base_url


def normalize_public_callback_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""

    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    scheme = parsed.scheme
    if scheme == "http" and hostname and not _is_internal_callback_host(hostname):
        scheme = "https"

    path = parsed.path or ""
    if path.startswith(("/app", "/desk", "/login", "/api")):
        path = ""

    netloc = parsed.netloc
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
    base_url = urlunsplit((scheme, netloc, path.rstrip("/"), "", ""))

    return base_url.rstrip("/")


def validate_public_callback_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url or "")
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or not hostname:
        frappe.throw(
            _(
                "Vobiz Webhook Base URL must be a public HTTPS URL. "
                "Set Vobiz Settings > Webhook Base URL, for example https://dev-sr.butest.tech."
            )
        )

    if _is_internal_callback_host(hostname):
        frappe.throw(_("Vobiz Webhook Base URL cannot be localhost or a private/internal host."))

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            frappe.throw(_("Vobiz Webhook Base URL must use a public internet reachable host."))
    except ValueError:
        pass


def _is_internal_callback_host(hostname: str) -> bool:
    hostname = (hostname or "").strip().lower()
    return hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost") or hostname.endswith(".local")


def build_callback_url(method_path: str, call_log: str, token: str, settings=None) -> str:
    base_url = get_webhook_base_url(settings)
    return f"{base_url}/api/method/{method_path}?call_log={quote(call_log)}&token={quote(token)}"
