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
        if self.queue_source not in {"CRM Lead", "Patient", "CRM Lead and Patient", "Patient Encounter", "Issue", "Discontinued"}:
            frappe.throw(_("Queue Source must be CRM Lead, Patient, CRM Lead and Patient, Patient Encounter, Issue, or Discontinued."))
        if self.queue_source in {"Patient", "CRM Lead and Patient"}:
            departments = _split_values(self.get("sr_medical_departments"))
            followup_ids = _split_values(self.get("sr_followup_ids"))
            if self.sr_medical_department and self.sr_medical_department not in departments:
                departments.insert(0, self.sr_medical_department)
            if self.sr_followup_id not in (None, "") and str(self.sr_followup_id) not in followup_ids:
                followup_ids.insert(0, str(self.sr_followup_id))
            if not departments:
                frappe.throw(_("At least one Department is required when Queue Source is Patient."))
            if not followup_ids:
                frappe.throw(_("At least one Follow up ID is required when Queue Source is Patient."))
            self.sr_medical_department = departments[0]
            self.sr_medical_departments = "\n".join(departments)
            self.sr_followup_id = followup_ids[0]
            self.sr_followup_ids = "\n".join(followup_ids)
        else:
            self.sr_medical_department = ""
            self.sr_followup_id = ""
            self.sr_medical_departments = ""
            self.sr_followup_ids = ""
        self.fallback_users = "\n".join(_split_values(self.get("fallback_users"), first=self.get("fallback_user")))
        if not self.fallback_user and self.fallback_users:
            self.fallback_user = _split_values(self.fallback_users)[0]
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

    def on_update(self):
        sync_reciprocal_fallback_users(self)


def _split_values(value: str | None, first: str | None = None) -> list[str]:
    values = []
    seen = set()
    for raw in [first or "", value or ""]:
        for row in str(raw).replace(",", "\n").splitlines():
            row = row.strip()
            if row and row not in seen:
                values.append(row)
                seen.add(row)
    return values


def sync_reciprocal_fallback_users(doc: VobizUserMapping) -> None:
    source_user = (doc.user or "").strip()
    if not source_user:
        return

    for fallback_user in _split_values(doc.get("fallback_users"), first=doc.get("fallback_user")):
        if fallback_user == source_user:
            continue
        fallback_mapping = frappe.db.get_value(
            "Vobiz User Mapping",
            {"user": fallback_user},
            ["name", "fallback_user", "fallback_users"],
            as_dict=True,
        )
        if not fallback_mapping:
            continue

        reciprocal_users = _split_values(
            fallback_mapping.get("fallback_users"),
            first=fallback_mapping.get("fallback_user"),
        )
        if source_user in reciprocal_users:
            continue

        reciprocal_users.append(source_user)
        frappe.db.set_value(
            "Vobiz User Mapping",
            fallback_mapping.name,
            {
                "fallback_user": fallback_mapping.get("fallback_user") or reciprocal_users[0],
                "fallback_users": "\n".join(reciprocal_users),
            },
            update_modified=True,
        )


@frappe.whitelist()
def get_team_leader(team: str | None = None) -> str:
    if "System Manager" not in frappe.get_roles() and "Vobiz Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted."))
    team = (team or "").strip()
    if not team or not frappe.db.exists("DocType", "Team"):
        return ""

    filters = {"team_name": team}
    if not frappe.db.exists("Team", filters):
        filters = {"name": team}
    return frappe.db.get_value("Team", filters, "team_lead") or ""
