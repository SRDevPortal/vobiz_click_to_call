import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

VISIBLE = "eval:doc.pe_shipkia_order_id || doc.pe_shipkia_shipment || doc.pe_latest_support_ticket || doc.ndr_tag || doc.ndr_latest_call || doc.ndr_note"

def execute():
    if not frappe.db.exists("Custom Field", "Patient Encounter-pe_shipment_tracking_tab"):
        return
    fields = [
        dict(fieldname="ndr_outcome_section", label="NDR Outcome", fieldtype="Section Break", insert_after="pe_latest_support_requested_on", depends_on=VISIBLE),
        dict(fieldname="ndr_tag", label="Latest Tag", fieldtype="Link", options="NDR Tag", insert_after="ndr_outcome_section", read_only=1, allow_on_submit=1),
        dict(fieldname="ndr_updated_by", label="Updated By", fieldtype="Link", options="User", insert_after="ndr_tag", read_only=1, allow_on_submit=1),
        dict(fieldname="ndr_outcome_column", fieldtype="Column Break", insert_after="ndr_updated_by"),
        dict(fieldname="ndr_follow_up_at", label="Call Ended At", fieldtype="Datetime", insert_after="ndr_outcome_column", read_only=1, allow_on_submit=1),
        dict(fieldname="ndr_updated_at", label="Outcome Saved At", fieldtype="Datetime", insert_after="ndr_follow_up_at", read_only=1, allow_on_submit=1),
        dict(fieldname="ndr_note_section", fieldtype="Section Break", insert_after="ndr_updated_at", depends_on=VISIBLE),
        dict(fieldname="ndr_note", label="Latest Note", fieldtype="Small Text", insert_after="ndr_note_section", read_only=1, allow_on_submit=1),
        dict(fieldname="ndr_call_section", fieldtype="Section Break", insert_after="ndr_note", depends_on=VISIBLE),
        dict(fieldname="ndr_latest_call", label="Latest Call", fieldtype="Link", options="Vobiz Call Log", insert_after="ndr_call_section", read_only=1, allow_on_submit=1),
        dict(fieldname="ndr_history_column", fieldtype="Column Break", insert_after="ndr_latest_call"),
        dict(fieldname="ndr_view_history", label="View NDR History", fieldtype="Button", insert_after="ndr_history_column"),
    ]
    create_custom_fields({"Patient Encounter": fields}, update=True)
    # Keep the shipment tab visible for retained NDR history even without shipment links.
    frappe.db.set_value("Custom Field", "Patient Encounter-pe_shipment_tracking_tab", "depends_on", VISIBLE)
    frappe.clear_cache(doctype="Patient Encounter")
