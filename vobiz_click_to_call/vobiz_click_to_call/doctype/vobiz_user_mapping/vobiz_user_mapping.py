from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.settings import get_caller_ids, get_settings


class VobizUserMapping(Document):
    def validate(self):
        settings = get_settings()
        default_country_code = settings.default_country_code or "+91"
        self.agent_mobile = normalize_phone_number(self.agent_mobile, default_country_code=default_country_code)
        if self.caller_id:
            self.caller_id = normalize_phone_number(self.caller_id, default_country_code=default_country_code)
            if self.caller_id not in get_caller_ids(settings):
                frappe.throw(_("Vobiz Number must be one of the Caller IDs configured in Vobiz Settings."))

        self.availability_status = self.availability_status or "Available"
        self.queue_source = self.queue_source or "CRM Lead"
        if self.queue_source not in {"CRM Lead", "Patient", "Discontinued"}:
            frappe.throw(_("Queue Source must be CRM Lead, Patient, or Discontinued."))
        if self.queue_source == "Patient":
            if not self.sr_medical_department:
                frappe.throw(_("Department is required when Queue Source is Patient."))
            if self.sr_followup_id in (None, ""):
                frappe.throw(_("Follow up ID is required when Queue Source is Patient."))
        else:
            self.sr_medical_department = ""
            self.sr_followup_id = ""
            self.fallback_user = ""
        if self.accept_calls is None:
            self.accept_calls = 1
        if self.auto_available_after_call is None:
            self.auto_available_after_call = 1
        if not self.last_status_at:
            self.last_status_at = frappe.utils.now()
        if self.enforce_working_hours and not (self.working_hours_start and self.working_hours_end):
            frappe.throw(_("Working Hours Start and End are required when working hours are enforced."))

        if self.enabled and not self.agent_mobile:
            frappe.throw(_("Agent Mobile is required for an enabled Vobiz user mapping."))
