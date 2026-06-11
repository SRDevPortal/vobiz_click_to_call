from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.settings import get_settings


class VobizUserMapping(Document):
    def validate(self):
        default_country_code = get_settings().default_country_code or "+91"
        self.agent_mobile = normalize_phone_number(self.agent_mobile, default_country_code=default_country_code)
        if self.caller_id:
            self.caller_id = normalize_phone_number(self.caller_id, default_country_code=default_country_code)

        self.availability_status = self.availability_status or "Available"
        self.queue_source = self.queue_source or "CRM Lead"
        if self.queue_source not in {"CRM Lead", "Patient"}:
            frappe.throw(_("Queue Source must be CRM Lead or Patient."))
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
