"""Scoped console projections and opaque phone choices; provider data stays original."""
from copy import deepcopy
import hashlib
import hmac
import json

import frappe

PREFIX = "privacy:v1:"
PHONE_FIELDS = frozenset({
    "mobile", "mobile_no", "phone", "phone_no", "contact_mobile", "contact_phone",
    "custom_whatsapp_number", "whatsapp_no", "sr_mobile_no", "sr_whatsapp_no",
    "alternate_phone", "sr_pe_mobile",
})


def restricted(user=None):
    if not frappe.conf.get("privacy_shield_desk_enabled", False):
        return False
    if "privacy_shield" not in frappe.get_installed_apps():
        return False
    from privacy_shield.policy import current_capabilities
    capabilities = current_capabilities(user) if user else current_capabilities()
    return not capabilities.view_full


def phone_choice(doctype, name, value):
    key = frappe.conf.get("encryption_key")
    if not key:
        raise frappe.ValidationError("Site encryption key is required for private phone selection.")
    message = json.dumps(
        [frappe.local.site, frappe.session.user, doctype, name, str(value).strip()],
        ensure_ascii=False, separators=(",", ":"),
    ).encode()
    return PREFIX + hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


def resolve_choice(doc, choice, candidates):
    if not isinstance(choice, str) or not choice.startswith(PREFIX):
        raise frappe.ValidationError("Invalid private phone choice.")
    for fieldname, value in candidates:
        if value and hmac.compare_digest(choice, phone_choice(doc.doctype, doc.name, value)):
            return value, fieldname
    raise frappe.PermissionError("Phone choice expired or does not belong to this record and user. Refresh and select again.")


def project_reference(row):
    if not restricted():
        return row
    from privacy_shield.masking import mask_number
    from privacy_shield.display_text import mask_display
    result = deepcopy(row)
    if result.get("phone"):
        result["phone_field"] = phone_choice(row["doctype"], row["name"], row["phone"])
        result["phone"] = mask_number(row["phone"])
        result["phone_masked"] = True
    for field in ("title", "company", "next_action", "whatsapp_last_message_preview"):
        if field in result:
            result[field] = mask_display(result[field])
    return result


def project_details(data):
    if not restricted():
        return data
    from privacy_shield.masking import mask_number
    from privacy_shield.display_text import mask_display
    result = deepcopy(data)
    for field in result.get("fields", []):
        if field.get("fieldname") in PHONE_FIELDS or field.get("fieldtype") == "Phone":
            field["value"] = mask_number(field.get("value"))
            field["read_only"] = 1
        elif field.get("fieldname") in {"patient_name", "first_name", "middle_name", "last_name", "subject", "description"}:
            field["value"] = mask_display(field.get("value"))
    return result


def project_choices(doc, choices):
    if not restricted():
        return choices
    from privacy_shield.masking import mask_number
    result = deepcopy(choices)
    for choice in result:
        original = choice["number"]
        choice["fieldname"] = phone_choice(doc.doctype, doc.name, original)
        choice["number"] = mask_number(original)
        choice["number_masked"] = True
    return result


def display_number(value, user=None):
    if not restricted(user):
        return value
    from privacy_shield.masking import mask_number
    return mask_number(value)


def project_call_rows(rows):
    """Project display-only call history; preserve routing identities and source rows."""
    if not restricted():
        return rows
    from privacy_shield.masking import mask_number
    from privacy_shield.display_text import mask_display
    result = deepcopy(rows)
    for row in result:
        if "customer_number" in row:
            row["customer_number"] = mask_number(row["customer_number"])
        for field in ("ai_summary", "ai_next_action", "transcript_text",
                      "transcript_error", "error_message", "hangup_cause"):
            if field in row:
                row[field] = mask_display(row[field])
    return result


def project_create_defaults(defaults):
    """Never pass a masked phone into a new document's persisted field."""
    if not restricted():
        return defaults
    result = deepcopy(defaults)
    appointment = result.get("Patient Appointment")
    if appointment is not None:
        # The Patient link's native fetch_from fills this field on server validation.
        appointment.pop("apt_mobile_number", None)
    return result
