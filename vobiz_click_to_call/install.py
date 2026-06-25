from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
    ensure_dependencies()
    ensure_defaults()


def after_migrate():
    ensure_dependencies()
    ensure_defaults()


def ensure_dependencies():
    installed_apps = set(frappe.get_installed_apps())
    if "vobiz_ai" not in installed_apps:
        frappe.throw(_("Install Vobiz AI before installing Vobiz Click To Call."))


def ensure_defaults():
    if not frappe.db.exists("DocType", "Vobiz Settings"):
        return

    settings = frappe.get_single("Vobiz Settings")
    changed = False

    defaults = {
        "base_url": "https://api.vobiz.ai/api/v1",
        "caller_ids": "",
        "default_country_code": "+91",
        "allowed_doctypes": "CRM Lead\nContact\nPatient\nCustomer",
        "default_call_flow": "Customer First",
        "manual_disposition_options": (
            "Connected\nNo Answer\nBusy\nFailed\nWrong Number\nNot Interested\nInterested\n"
            "Follow Up Required\nConverted\nCall Back Later\nLanguage Issue\nDuplicate Lead\n"
            "Invalid Number\nDo Not Call"
        ),
        "agent_ring_timeout": 30,
        "http_timeout": 20,
        "max_call_duration": 3600,
        "enable_end_fallback": 0,
        "end_fallback_mobile": "",
        "enable_busy_callback_ai_fallback": 0,
        "busy_callback_ai_fallback_mobile": "",
        "store_raw_payloads": 1,
        "prevent_blocked_numbers": 1,
        "max_call_attempts_per_reference_per_day": 0,
        "max_calls_per_user_per_day": 0,
        "enable_cdr_sync": 1,
        "cdr_sync_lookback_days": 7,
        "enable_recording": 0,
        "recording_format": "mp3",
        "record_channel_type": "stereo",
        "recording_time_limit": 3600,
        "enable_transcription": 0,
        "transcription_type": "auto",
        "enable_ai_disposition": 0,
        "openai_model": "gpt-4.1-mini",
        "ai_confidence_threshold": 0.75,
        "ai_disposition_options": (
            "Interested\nNot Interested\nFollow Up\nCallback Requested\nWrong Number\nNo Requirement\n"
            "Converted\nComplaint\nDo Not Call\nUnknown"
        ),
        "auto_apply_ai_disposition": 0,
        "add_ai_summary_comment": 1,
    }

    for fieldname, value in defaults.items():
        if not settings.get(fieldname):
            settings.set(fieldname, value)
            changed = True

    if changed:
        settings.save(ignore_permissions=True)

    ensure_crm_lead_fields()
    ensure_crm_lead_disposition_optional()
    ensure_vobiz_call_log_disposition_field()


def ensure_vobiz_call_log_disposition_field(extra_options: list[str] | None = None):
    if not frappe.db.exists("DocType", "Vobiz Call Log"):
        return

    meta = frappe.get_meta("Vobiz Call Log")
    if not meta.has_field("disposition"):
        return

    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    options = get_vobiz_call_log_disposition_options(extra_options=extra_options)
    if options:
        make_property_setter("Vobiz Call Log", "disposition", "fieldtype", "Select", "Data", validate_fields_for_doctype=False)
        make_property_setter("Vobiz Call Log", "disposition", "options", options, "Text", validate_fields_for_doctype=False)
    else:
        make_property_setter("Vobiz Call Log", "disposition", "fieldtype", "Data", "Data", validate_fields_for_doctype=False)
        make_property_setter("Vobiz Call Log", "disposition", "options", "", "Text", validate_fields_for_doctype=False)
    make_property_setter("Vobiz Call Log", "disposition", "reqd", "0", "Check", validate_fields_for_doctype=False)
    frappe.clear_cache(doctype="Vobiz Call Log")


def ensure_crm_lead_disposition_optional():
    if not frappe.db.exists("DocType", "CRM Lead"):
        return

    fields = ("sr_lead_disposition", "lead_disposition", "disposition")
    meta = frappe.get_meta("CRM Lead")
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    for fieldname in fields:
        if not meta.has_field(fieldname):
            continue
        make_property_setter("CRM Lead", fieldname, "reqd", "0", "Check", validate_fields_for_doctype=False)
        make_property_setter(
            "CRM Lead",
            fieldname,
            "mandatory_depends_on",
            "",
            "Text",
            validate_fields_for_doctype=False,
        )
        custom_field = frappe.db.get_value("Custom Field", {"dt": "CRM Lead", "fieldname": fieldname}, "name")
        if custom_field:
            frappe.db.set_value(
                "Custom Field",
                custom_field,
                {"reqd": 0, "mandatory_depends_on": ""},
                update_modified=False,
            )

    frappe.clear_cache(doctype="CRM Lead")


def get_vobiz_call_log_disposition_options(extra_options: list[str] | None = None) -> str:
    values: list[str] = []
    if frappe.db.exists("DocType", "SR Lead Disposition"):
        try:
            from vobiz_click_to_call.services.lead_disposition import get_lead_disposition_options

            values.extend(get_lead_disposition_options())
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Vobiz Call Log disposition options sync failed")

    values.extend(extra_options or [])
    cleaned = []
    seen = set()
    for value in values:
        value = (value or "").strip()
        if value and value not in seen:
            cleaned.append(value)
            seen.add(value)
    return "\n" + "\n".join(cleaned) if cleaned else ""


def ensure_crm_lead_fields():
    if not frappe.db.exists("DocType", "CRM Lead"):
        return

    create_custom_fields(
        {
            "CRM Lead": [
                {
                    "fieldname": "vobiz_calling_section",
                    "label": "Vobiz Calling",
                    "fieldtype": "Section Break",
                    "insert_after": "mobile_no",
                    "collapsible": 1,
                },
                {
                    "fieldname": "vobiz_do_not_call",
                    "label": "Do Not Call",
                    "fieldtype": "Check",
                    "insert_after": "vobiz_calling_section",
                    "read_only": 1,
                },
                {
                    "fieldname": "vobiz_do_not_call_reason",
                    "label": "Do Not Call Reason",
                    "fieldtype": "Small Text",
                    "insert_after": "vobiz_do_not_call",
                    "read_only": 1,
                },
                {
                    "fieldname": "vobiz_last_call_status",
                    "label": "Last Call Status",
                    "fieldtype": "Data",
                    "insert_after": "vobiz_do_not_call_reason",
                    "read_only": 1,
                    "in_list_view": 1,
                },
                {
                    "fieldname": "vobiz_last_call_time",
                    "label": "Last Call Time",
                    "fieldtype": "Datetime",
                    "insert_after": "vobiz_last_call_status",
                    "read_only": 1,
                },
                {
                    "fieldname": "vobiz_last_called_by",
                    "label": "Last Called By",
                    "fieldtype": "Link",
                    "options": "User",
                    "insert_after": "vobiz_last_call_time",
                    "read_only": 1,
                },
                {
                    "fieldname": "vobiz_last_disposition",
                    "label": "Last Disposition",
                    "fieldtype": "Data",
                    "insert_after": "vobiz_last_called_by",
                    "read_only": 1,
                    "in_list_view": 1,
                },
                {
                    "fieldname": "vobiz_next_follow_up",
                    "label": "Next Follow-up",
                    "fieldtype": "Datetime",
                    "insert_after": "vobiz_last_disposition",
                    "read_only": 1,
                },
                {
                    "fieldname": "vobiz_call_counts_column",
                    "fieldtype": "Column Break",
                    "insert_after": "vobiz_next_follow_up",
                },
                {
                    "fieldname": "vobiz_total_call_attempts",
                    "label": "Total Call Attempts",
                    "fieldtype": "Int",
                    "insert_after": "vobiz_call_counts_column",
                    "read_only": 1,
                },
                {
                    "fieldname": "vobiz_connected_call_count",
                    "label": "Connected Call Count",
                    "fieldtype": "Int",
                    "insert_after": "vobiz_total_call_attempts",
                    "read_only": 1,
                },
                {
                    "fieldname": "vobiz_missed_call_count",
                    "label": "Missed Call Count",
                    "fieldtype": "Int",
                    "insert_after": "vobiz_connected_call_count",
                    "read_only": 1,
                },
            ]
        },
        update=True,
    )
