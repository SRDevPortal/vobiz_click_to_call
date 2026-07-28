from __future__ import annotations

import frappe


DOCTYPE = "Vobiz Call Log"
PROPERTY_SETTER_NAMES = (
    f"{DOCTYPE}-disposition-fieldtype",
    f"{DOCTYPE}-disposition-options",
    f"{DOCTYPE}-disposition-reqd",
)


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        return

    frappe.db.delete("Property Setter", {"name": ("in", PROPERTY_SETTER_NAMES)})
    frappe.clear_cache(doctype=DOCTYPE)
