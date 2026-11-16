import frappe

TAG_NAMES = {
    "Customer Confirmed Delivery": "Delivery Confirmed",
    "Reattempt Requested": "Reattempt",
    "Customer Requested Callback": "Callback",
    "Customer Unreachable": "Unreachable",
    "Address Correction Required": "Address Update",
    "Customer Refused Delivery": "Refused",
    "Self Pickup Requested": "Self Pickup",
    "Escalation Required": "Escalate",
}

def execute():
    for old, short in TAG_NAMES.items():
        if frappe.db.exists("NDR Tag", old):
            if frappe.db.exists("NDR Tag", short):
                frappe.throw("NDR tag name already exists: " + short + ". Review before merging.")
            # Frappe updates Link fields on encounters and interaction history.
            frappe.rename_doc("NDR Tag", old, short, force=True)
            frappe.db.set_value("NDR Tag", short, "tag_name", short)
    frappe.clear_cache(doctype="NDR Tag")
