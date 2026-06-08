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
