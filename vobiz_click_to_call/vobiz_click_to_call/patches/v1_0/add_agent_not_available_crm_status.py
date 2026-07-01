import frappe


def execute():
    doctype = "CRM Lead Status"
    status_name = "Agent Not Available"

    if not frappe.db.exists("DocType", doctype):
        return
    if frappe.db.exists(doctype, status_name):
        return

    doc = frappe.get_doc(
        {
            "doctype": doctype,
            "lead_status": status_name,
            "type": "Open",
        }
    )
    doc.insert(ignore_permissions=True)
