from __future__ import annotations

import frappe
from frappe import _

from vobiz_click_to_call.services.disposition import (
    get_reference_call_summary as get_summary,
    save_call_disposition,
)
from vobiz_click_to_call.services.lead_disposition import get_lead_disposition_context
from vobiz_click_to_call.services.settings import get_disposition_options, get_manual_disposition_options


@frappe.whitelist()
def get_disposition_options_api(
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    lead_status: str | None = None,
) -> list[str]:
    return get_manual_disposition_options(
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        lead_status=lead_status,
    )


@frappe.whitelist()
def get_lead_disposition_context_api(
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    lead_status: str | None = None,
) -> dict:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    return get_lead_disposition_context(reference_doctype, reference_name, lead_status)


@frappe.whitelist()
def save_disposition(
    call_log: str,
    disposition: str,
    notes: str = "",
    lead_status: str | None = None,
    follow_up_datetime: str | None = None,
    mark_dnd: int | str | bool = 0,
) -> dict:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    return save_call_disposition(
        call_log=call_log,
        disposition=disposition,
        notes=notes,
        lead_status=lead_status,
        follow_up_datetime=follow_up_datetime,
        mark_dnd=bool(frappe.utils.cint(mark_dnd)),
    )


@frappe.whitelist()
def get_reference_call_summary(reference_doctype: str, reference_name: str) -> dict:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    return get_summary(reference_doctype, reference_name)


@frappe.whitelist()
def get_ai_disposition_options() -> list[str]:
    return get_disposition_options()
