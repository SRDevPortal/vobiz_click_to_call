from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from vobiz_click_to_call.services.numbers import normalize_phone_number
from vobiz_click_to_call.services.settings import get_caller_ids, get_settings


class VobizIncomingMapping(Document):
    def before_naming(self):
        self._normalize_did_fields()
        if not self.normalized_did:
            frappe.throw(_("DID Number is required for incoming mapping."))

    def validate(self):
        self._normalize_did_fields()
        settings = get_settings()
        default_country_code = settings.default_country_code or "+91"
        self.routing_strategy = self.routing_strategy or "Round Robin"
        self.default_lead_status = self.default_lead_status or "Select Option"

        if self.routing_strategy not in {"Round Robin", "Load Balancing"}:
            frappe.throw(_("Routing Strategy must be Round Robin or Load Balancing."))

        caller_ids = get_caller_ids(settings)
        if self.did_number and caller_ids and self.did_number not in caller_ids:
            frappe.throw(_("DID Number must be one of the Caller IDs configured in Vobiz Settings."))

        seen_agents = set()
        for row in self.get("agents") or []:
            if row.agent_mobile:
                row.agent_mobile = normalize_phone_number(row.agent_mobile, default_country_code=default_country_code)
            if row.agent_user:
                if row.agent_user in seen_agents:
                    frappe.throw(_("Agent {0} is already added to this incoming mapping.").format(row.agent_user))
                seen_agents.add(row.agent_user)

        if self.enabled and not self.did_number:
            frappe.throw(_("DID Number is required for an enabled incoming mapping."))

    def _normalize_did_fields(self):
        settings = get_settings()
        default_country_code = settings.default_country_code or "+91"
        self.did_number = normalize_phone_number(self.did_number, default_country_code=default_country_code)
        self.normalized_did = self.did_number
