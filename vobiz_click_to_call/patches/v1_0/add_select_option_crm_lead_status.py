from __future__ import annotations

import frappe


def execute():
    if not frappe.db.exists("DocType", "CRM Lead Status"):
        return
    if frappe.db.exists("CRM Lead Status", "Select Option"):
        return

    doc = frappe.new_doc("CRM Lead Status")
    doc.name = "Select Option"
    if doc.meta.has_field("lead_status"):
        doc.lead_status = "Select Option"
    if doc.meta.has_field("status"):
        doc.status = "Select Option"
    if doc.meta.has_field("type"):
        doc.type = "Open"
    doc.insert(ignore_permissions=True)
