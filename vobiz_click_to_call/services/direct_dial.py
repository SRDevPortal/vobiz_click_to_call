"""Permission-checked NDR destinations; never create leads or patients."""
import json
import re

import frappe
from frappe import _
from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.queue_sources import queue_includes
from vobiz_click_to_call.services.settings import get_allowed_doctypes, get_default_country_code, get_settings


def allowed(mapping, settings=None):
    if not (mapping and frappe.utils.cint(mapping.get("enabled", 1))
            and queue_includes(mapping.get("queue_source"), "NDR")):
        return False
    settings = get_settings() if settings is None else settings
    return bool(frappe.utils.cint(settings.get("enable_ndr_dialpad")))


def assert_allowed(mapping, settings=None):
    if frappe.session.user == "Guest" or not allowed(mapping, settings):
        frappe.throw(_("Direct dialing requires Enable NDR Dialpad in Vobiz Settings and an enabled NDR agent mapping."), frappe.PermissionError)


def normalize_destination(value, settings):
    if not isinstance(value, str) or not re.fullmatch(r"[+]?[0-9 ()\-]{7,32}", value.strip()):
        frappe.throw(_("Enter a valid phone number, including the country code for international calls."))
    number = normalize_phone_number(value, default_country_code=get_default_country_code(settings))
    if not re.fullmatch(r"[+][1-9][0-9]{7,14}", number):
        frappe.throw(_("Enter a valid phone number."))
    if number.startswith("+91") and len(number) != 13:
        frappe.throw(_("Enter a complete 10-digit Indian phone number."))
    return number


def resolve_target(value, mapping, settings):
    assert_allowed(mapping, settings)
    number = normalize_destination(value, settings)
    # Exact variants only. No suffix/LIKE scans and no access through get_all.
    variants = {number, number[1:], "00" + number[1:], value.strip()}
    country = get_default_country_code(settings)
    if number.startswith(country) and len(number[len(country):]) == 10:
        variants.add(number[len(country):])
        variants.add("0" + number[len(country):])
    matches = []
    for doctype, fields in (("CRM Lead", ("mobile_no", "phone")), ("Patient", ("mobile", "phone"))):
        if doctype not in get_allowed_doctypes(settings) or not frappe.db.exists("DocType", doctype):
            continue
        if not frappe.has_permission(doctype, "read"):
            continue
        meta = frappe.get_meta(doctype)
        fields = [field for field in fields if meta.has_field(field)]
        if not fields:
            continue
        rows = frappe.get_list(doctype, fields=["name", *fields],
            or_filters=[[field, "in", sorted(variants)] for field in fields], limit_page_length=2)
        for row in rows:
            for field in fields:
                if normalize_phone_number(row.get(field), default_country_code=country) == number:
                    matches.append((doctype, row.name, field))
                    break
    if len(matches) == 1:
        doctype, name, field = matches[0]
        doc = frappe.get_doc(doctype, name)
        if doc.has_permission("read"):
            # Recheck current value rather than trust a stale list result.
            if normalize_phone_number(doc.get(field), default_country_code=country) == number:
                return number, doctype, name, field
    return number, "", "", None


def resolve_retry_target(call_log, mapping, settings):
    """Keep the original target even if a matching reference was created later."""
    assert_allowed(mapping, settings)
    previous = frappe.get_doc("Vobiz Call Log", call_log)
    if previous.user != frappe.session.user or not is_direct_call(previous):
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    number = normalize_destination(previous.customer_number, settings)
    doctype, name = previous.reference_doctype or "", previous.reference_name or ""
    field = previous.get("phone_field")
    if doctype and name:
        if doctype not in get_allowed_doctypes(settings):
            frappe.throw(_("Calling is no longer enabled for this record."))
        doc = frappe.get_doc(doctype, name)
        if not doc.has_permission("read"):
            frappe.throw(_("You no longer have access to this record."), frappe.PermissionError)
        if not field or normalize_phone_number(doc.get(field), default_country_code=get_default_country_code(settings)) != number:
            frappe.throw(_("The record's phone number changed. Start a new call."))
    return number, doctype, name, field


def is_direct_call(doc):
    try:
        context = json.loads(doc.get("request_json") or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(context, dict) and context.get("direct_dial") is True


def result(message):
    if message.get("call_log"):
        row = frappe.db.get_value("Vobiz Call Log", message["call_log"],
                                  ["reference_doctype", "reference_name"], as_dict=True) or {}
        message.update(direct_dial=True, reference_doctype=row.get("reference_doctype") or "",
                       reference_name=row.get("reference_name") or "")
    return message
