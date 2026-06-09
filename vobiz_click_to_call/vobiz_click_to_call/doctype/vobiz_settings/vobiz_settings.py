from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from vobiz_click_to_call.services.ai import DEFAULT_AI_DISPOSITION_SYSTEM_PROMPT
from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.settings import normalize_public_callback_base_url, validate_public_callback_base_url


class VobizSettings(Document):
    def validate(self):
        self.base_url = (self.base_url or "https://api.vobiz.ai/api/v1").strip().rstrip("/")
        self.default_country_code = (self.default_country_code or "+91").strip()
        if self.default_caller_id:
            self.default_caller_id = normalize_phone_number(
                self.default_caller_id,
                default_country_code=self.default_country_code,
            )
        self.allowed_doctypes = (self.allowed_doctypes or "CRM Lead\nContact\nPatient\nCustomer").strip()
        self.default_call_flow = self.default_call_flow or "Customer First"
        if self.webhook_base_url:
            self.webhook_base_url = normalize_public_callback_base_url(self.webhook_base_url)
        self.manual_disposition_options = (
            self.manual_disposition_options
            or "Connected\nNo Answer\nBusy\nFailed\nWrong Number\nNot Interested\nInterested\n"
            "Follow Up Required\nConverted\nCall Back Later\nLanguage Issue\nDuplicate Lead\n"
            "Invalid Number\nDo Not Call"
        )
        self.agent_ring_timeout = self.agent_ring_timeout or 30
        self.max_call_duration = self.max_call_duration or 3600
        self.max_call_attempts_per_reference_per_day = self.max_call_attempts_per_reference_per_day or 0
        self.max_calls_per_user_per_day = self.max_calls_per_user_per_day or 0
        self.http_timeout = self.http_timeout or 20
        self.cdr_sync_lookback_days = self.cdr_sync_lookback_days or 7
        self.recording_format = self.recording_format or "mp3"
        self.record_channel_type = self.record_channel_type or "stereo"
        self.recording_time_limit = self.recording_time_limit or self.max_call_duration or 3600
        self.transcription_type = self.transcription_type or "auto"
        self.openai_model = self.openai_model or "gpt-4.1-mini"
        self.ai_confidence_threshold = self.ai_confidence_threshold or 0.75
        if self.meta.has_field("ai_disposition_system_prompt"):
            self.ai_disposition_system_prompt = (
                self.get("ai_disposition_system_prompt") or DEFAULT_AI_DISPOSITION_SYSTEM_PROMPT
            ).strip()
        self.sync_ai_disposition_options()

        if not self.enabled:
            return

        auth_token = None
        try:
            auth_token = self.get_password("auth_token")
        except Exception:
            auth_token = None

        auth_id = self.auth_id or frappe.conf.get("vobiz_auth_id")
        auth_token = auth_token or frappe.conf.get("vobiz_auth_token")

        if not (auth_id and auth_token):
            frappe.throw(_("Vobiz Auth ID and Auth Token are required when Vobiz Settings is enabled."))

        if not (self.default_caller_id or frappe.conf.get("vobiz_default_caller_id")):
            frappe.throw(_("Default Caller ID is required when Vobiz Settings is enabled."))

        validate_public_callback_base_url(self.webhook_base_url or frappe.conf.get("vobiz_webhook_base_url") or frappe.utils.get_url())

    def sync_ai_disposition_options(self) -> list[str]:
        options = get_sr_lead_disposition_options()
        if options:
            self.ai_disposition_options = "\n".join(options)
        else:
            self.ai_disposition_options = (
                self.ai_disposition_options
                or "Interested\nNot Interested\nFollow Up\nCallback Requested\nWrong Number\nNo Requirement\nConverted\nComplaint\nDo Not Call\nUnknown"
            )
        return options


def get_sr_lead_disposition_options() -> list[str]:
    try:
        from vobiz_click_to_call.services.lead_disposition import get_lead_disposition_rows

        rows = get_lead_disposition_rows()
        return [row["name"] for row in rows if row.get("name")]
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Vobiz Settings SR Lead Disposition sync failed")
        return []


@frappe.whitelist()
def sync_ai_disposition_options() -> dict:
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted."))

    settings = frappe.get_single("Vobiz Settings")
    options = settings.sync_ai_disposition_options()
    settings.save(ignore_permissions=True)
    frappe.db.commit()
    return {
        "count": len(options),
        "options": settings.ai_disposition_options or "",
    }
