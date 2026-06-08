from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from vobiz_click_to_call.services.numbers import normalize_phone_number


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
        self.ai_disposition_options = (
            self.ai_disposition_options
            or "Interested\nNot Interested\nFollow Up\nCallback Requested\nWrong Number\nNo Requirement\nConverted\nComplaint\nDo Not Call\nUnknown"
        )

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
