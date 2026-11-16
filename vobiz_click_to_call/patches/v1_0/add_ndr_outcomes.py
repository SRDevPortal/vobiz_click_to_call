import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
TAGS = 'Customer Confirmed Delivery\nReattempt Requested\nCustomer Requested Callback\nCustomer Unreachable\nAddress Correction Required\nCustomer Refused Delivery\nSelf Pickup Requested\nEscalation Required'
def execute():
    frappe.reload_doc("vobiz_click_to_call", "doctype", "ndr_tag")
    frappe.reload_doc("vobiz_click_to_call", "doctype", "ndr_interaction_log")
    create_custom_fields({
        "Vobiz Settings": [
            dict(fieldname="enable_ndr_outcomes", label="Enable NDR Outcomes", fieldtype="Check", default="0", insert_after="enable_ndr_queue"),
            dict(fieldname="ndr_outcome_tags", label="NDR Outcome Tags", fieldtype="Small Text", default=TAGS, insert_after="enable_ndr_outcomes")],
        "Vobiz Call Log": [
            dict(fieldname="ndr_encounter", label="NDR Encounter", fieldtype="Link", options="Patient Encounter", read_only=1),
            dict(fieldname="ndr_outcome_state", label="NDR Outcome State", fieldtype="Select", options="\nPending\nSaved", read_only=1)],
        "Patient Encounter": [
            dict(fieldname="ndr_tag", label="Latest NDR Tag", fieldtype="Data", read_only=1, allow_on_submit=1),
            dict(fieldname="ndr_note", label="Latest NDR Note", fieldtype="Small Text", read_only=1, allow_on_submit=1),
            dict(fieldname="ndr_follow_up_at", label="NDR Follow-up At", fieldtype="Datetime", read_only=1, allow_on_submit=1),
            dict(fieldname="ndr_updated_by", label="NDR Updated By", fieldtype="Link", options="User", read_only=1, allow_on_submit=1),
            dict(fieldname="ndr_updated_at", label="NDR Updated At", fieldtype="Datetime", read_only=1, allow_on_submit=1),
            dict(fieldname="ndr_latest_call", label="Latest NDR Call", fieldtype="Link", options="Vobiz Call Log", read_only=1, allow_on_submit=1)]
    }, update=True)
    if not frappe.db.get_single_value("Vobiz Settings", "ndr_outcome_tags"):
        frappe.db.set_single_value("Vobiz Settings", "ndr_outcome_tags", TAGS)

    frappe.db.add_index("Vobiz Call Log", ["user", "ndr_outcome_state", "creation"], "idx_ndr_pending")
