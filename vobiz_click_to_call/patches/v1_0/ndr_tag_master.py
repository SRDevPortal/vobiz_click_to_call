import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from vobiz_click_to_call.patches.v1_0.add_ndr_outcomes import TAGS
from vobiz_click_to_call.patches.v1_0.shorten_ndr_tags import TAG_NAMES

def execute():
    frappe.reload_doc("vobiz_click_to_call", "doctype", "ndr_tag")
    names = set((frappe.db.get_single_value("Vobiz Settings", "ndr_outcome_tags") or TAGS).splitlines())
    # Preserve links to all previously recorded outcomes.
    for doctype, field in (("NDR Interaction Log", "tag"), ("Patient Encounter", "ndr_tag")):
        if frappe.db.has_column(doctype, field):
            names.update(frappe.get_all(doctype, filters={field: ["is", "set"]}, pluck=field, distinct=True))
    for name in sorted(str(v).strip() for v in names if v and str(v).strip()):
        if name in TAG_NAMES and frappe.db.exists("NDR Tag", TAG_NAMES[name]):
            continue
        if not frappe.db.exists("NDR Tag", name):
            frappe.get_doc({"doctype": "NDR Tag", "tag_name": name, "enabled": 1}).insert(ignore_permissions=True)
    frappe.reload_doc("vobiz_click_to_call", "doctype", "ndr_interaction_log")
    # Data and Link share varchar storage. All existing values were seeded above.
    # Frappe's form validator disallows this metadata conversion, so migrate it explicitly.
    if frappe.db.exists("Custom Field", "Patient Encounter-ndr_tag"):
        frappe.db.set_value("Custom Field", "Patient Encounter-ndr_tag",
                            {"fieldtype": "Link", "options": "NDR Tag"})
        frappe.clear_cache(doctype="Patient Encounter")
    create_custom_fields({
        "Patient Encounter": [dict(fieldname="ndr_tag", label="Latest NDR Tag", fieldtype="Link", options="NDR Tag", read_only=1, allow_on_submit=1)],
        "Vobiz Call Log": [dict(fieldname="ndr_hangup_at", label="NDR Hangup At", fieldtype="Datetime", read_only=1)]
    }, update=True)
    if frappe.db.exists("Custom Field", "Vobiz Settings-ndr_outcome_tags"):
        frappe.db.set_value("Custom Field", "Vobiz Settings-ndr_outcome_tags", "hidden", 1)
    frappe.clear_cache()
