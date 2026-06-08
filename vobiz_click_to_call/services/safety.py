from __future__ import annotations

from datetime import datetime, time

import frappe
from frappe import _

from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.settings import get_default_country_code, get_settings


BLOCKED_REFERENCE_FIELDS = ("vobiz_do_not_call", "do_not_call", "do_not_contact")


def assert_call_allowed(
    *,
    customer_number: str,
    reference_doctype: str,
    reference_name: str,
    user: str,
    mapping: dict,
    settings=None,
) -> None:
    settings = settings or get_settings()

    if settings.prevent_blocked_numbers:
        reason = get_blocked_reason(customer_number, reference_doctype, reference_name)
        if reason:
            frappe.throw(reason)

    if settings.max_call_attempts_per_reference_per_day:
        attempts = get_attempt_count_for_reference_today(reference_doctype, reference_name)
        if attempts >= int(settings.max_call_attempts_per_reference_per_day):
            frappe.throw(_("Daily call attempt limit reached for this record."))

    if settings.max_calls_per_user_per_day:
        attempts = get_attempt_count_for_user_today(user)
        if attempts >= int(settings.max_calls_per_user_per_day):
            frappe.throw(_("Daily call limit reached for your user."))

    working_hours_reason = get_working_hours_block_reason(mapping)
    if working_hours_reason:
        frappe.throw(working_hours_reason)


def get_blocked_reason(customer_number: str, reference_doctype: str | None = None, reference_name: str | None = None) -> str:
    if reference_doctype and reference_name and frappe.db.exists(reference_doctype, reference_name):
        meta = frappe.get_meta(reference_doctype)
        for fieldname in BLOCKED_REFERENCE_FIELDS:
            if meta.get_field(fieldname) and frappe.db.get_value(reference_doctype, reference_name, fieldname):
                return _("This record is marked Do Not Call.")

    normalized = normalize_phone_number(customer_number, default_country_code=get_default_country_code())
    if not normalized or not frappe.db.exists("DocType", "Vobiz Blocked Number"):
        return ""

    blocked = frappe.db.get_value(
        "Vobiz Blocked Number",
        {"normalized_phone_number": normalized, "enabled": 1},
        ["reason", "name"],
        as_dict=True,
    )
    if not blocked:
        return ""

    return _("This number is blocked for Vobiz calls: {0}.").format(blocked.reason or blocked.name)


def block_number(
    *,
    phone_number: str,
    reason: str = "Do Not Call",
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    notes: str | None = None,
) -> str:
    normalized = normalize_phone_number(phone_number, default_country_code=get_default_country_code())
    if not normalized:
        frappe.throw(_("Phone number is required to block a number."))

    existing = frappe.db.exists("Vobiz Blocked Number", {"normalized_phone_number": normalized})
    if existing:
        doc = frappe.get_doc("Vobiz Blocked Number", existing)
        doc.enabled = 1
        doc.reason = reason or doc.reason or "Do Not Call"
        doc.reference_doctype = reference_doctype or doc.reference_doctype
        doc.reference_name = reference_name or doc.reference_name
        doc.notes = notes or doc.notes
        doc.save(ignore_permissions=True)
        return doc.name

    doc = frappe.get_doc(
        {
            "doctype": "Vobiz Blocked Number",
            "enabled": 1,
            "phone_number": phone_number,
            "normalized_phone_number": normalized,
            "reason": reason or "Do Not Call",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "notes": notes,
            "blocked_by": frappe.session.user,
            "blocked_at": frappe.utils.now(),
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def get_attempt_count_for_reference_today(reference_doctype: str, reference_name: str) -> int:
    return frappe.db.count(
        "Vobiz Call Log",
        {
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "creation": ["between", today_bounds()],
        },
    )


def get_attempt_count_for_user_today(user: str) -> int:
    return frappe.db.count(
        "Vobiz Call Log",
        {
            "user": user,
            "creation": ["between", today_bounds()],
        },
    )


def today_bounds() -> list[str]:
    today = frappe.utils.today()
    return [f"{today} 00:00:00", f"{today} 23:59:59"]


def get_working_hours_block_reason(mapping: dict) -> str:
    if not frappe.utils.cint(mapping.get("enforce_working_hours")):
        return ""

    now_dt = frappe.utils.now_datetime()
    allowed_days = {
        day.strip().lower()
        for day in str(mapping.get("working_days") or "").replace("\n", ",").split(",")
        if day.strip()
    }
    if allowed_days and now_dt.strftime("%A").lower() not in allowed_days:
        return _("You are outside your configured Vobiz working days.")

    start = parse_time(mapping.get("working_hours_start"))
    end = parse_time(mapping.get("working_hours_end"))
    if not start or not end:
        return ""

    current = now_dt.time()
    if start <= end:
        inside = start <= current <= end
    else:
        inside = current >= start or current <= end

    return "" if inside else _("You are outside your configured Vobiz working hours.")


def parse_time(value) -> time | None:
    if not value:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    try:
        return datetime.strptime(str(value).split(".")[0], "%H:%M:%S").time()
    except Exception:
        try:
            return datetime.strptime(str(value), "%H:%M").time()
        except Exception:
            return None
