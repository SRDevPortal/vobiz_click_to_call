"""Repair unsupported historical statuses without declaring a call ended."""
import frappe
from vobiz_click_to_call.services.confirmation import LEGACY_STATUSES, normalize_legacy_status


def execute():
    while True:
        names = frappe.get_all("Vobiz Call Log", filters={"status": ["in", list(LEGACY_STATUSES)]},
                               pluck="name", order_by="name", limit_page_length=100)
        if not names:
            return
        for name in names:
            doc = frappe.get_doc("Vobiz Call Log", name, for_update=True)
            if doc.status in LEGACY_STATUSES:
                normalize_legacy_status(doc)
                # Historical call logs may reference deleted records.
                doc.flags.ignore_links = True
                doc.save(ignore_permissions=True)
        frappe.db.commit()
