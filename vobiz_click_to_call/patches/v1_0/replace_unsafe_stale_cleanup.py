"""Retire site scripts superseded by guarded, provider-aware app recovery."""
import frappe


def execute():
    for name in ("Vobiz Stale Call Cleanup", "Ringing Call Connect Issue"):
        if not frappe.db.exists("Server Script", name):
            continue
        doc = frappe.get_doc("Server Script", name)
        if (doc.script_type == "Scheduler Event" and not doc.disabled
                and "Vobiz User Mapping" in (doc.script or "")
                and "current_call_log" in (doc.script or "")):
            doc.disabled = 1
            doc.save(ignore_permissions=True)
