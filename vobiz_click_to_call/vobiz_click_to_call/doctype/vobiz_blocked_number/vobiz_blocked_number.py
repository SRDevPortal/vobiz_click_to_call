from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.settings import get_default_country_code


class VobizBlockedNumber(Document):
    def before_naming(self):
        self.set_normalized_phone_number()

    def validate(self):
        self.set_normalized_phone_number()
        if not self.normalized_phone_number:
            frappe.throw(_("Phone Number is required."))

        self.reason = self.reason or "Do Not Call"
        self.blocked_by = self.blocked_by or frappe.session.user
        self.blocked_at = self.blocked_at or frappe.utils.now()

    def set_normalized_phone_number(self):
        self.normalized_phone_number = normalize_phone_number(
            self.phone_number,
            default_country_code=get_default_country_code(),
        )
