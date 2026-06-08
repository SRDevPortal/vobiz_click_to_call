from __future__ import annotations

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from vobiz_click_to_call.services.cdr import sync_call_log_cdr, sync_recent_cdrs


@frappe.whitelist()
@rate_limit(limit=6, seconds=60)
def sync_call_log(call_log: str) -> dict:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    return sync_call_log_cdr(call_log)


@frappe.whitelist()
@rate_limit(limit=2, seconds=60)
def sync_recent(limit: int = 50) -> dict:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only System Manager can sync recent Vobiz CDRs."))
    return sync_recent_cdrs(limit=limit)
