import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
    frappe.reload_doc("vobiz_click_to_call", "doctype", "ndr_interaction_log")
    create_custom_fields({"Patient Encounter": [
        dict(fieldname="ndr_shipment_column", fieldtype="Column Break", insert_after="pe_shipkia_shipment"),
        dict(fieldname="ndr_support_section", fieldtype="Section Break", insert_after="pe_shipkia_delivered_on"),
        dict(fieldname="ndr_callback_at", label="Callback Scheduled At", fieldtype="Datetime", read_only=1, allow_on_submit=1, insert_after="ndr_updated_at", depends_on="eval:doc.ndr_tag === 'Callback' || doc.ndr_callback_at"),
        dict(fieldname="ndr_actions_html", fieldtype="HTML", insert_after="ndr_call_section")
    ]}, update=True)
    changes = {
        "pe_shipment_tracking_section": {"label": "Shipment Details", "collapsible": 0},
        "pe_shipkia_order_id": {"label": "Order ID"},
        "pe_shipkia_stage": {"insert_after": "pe_shipkia_order_id", "label": "Stage"},
        "pe_delivery_partner": {"insert_after": "pe_shipkia_stage", "label": "Courier"},
        "pe_shipkia_shipment": {"insert_after": "pe_delivery_partner"},
        "pe_shipkia_awb_number": {"insert_after": "ndr_shipment_column", "label": "AWB Number"},
        "pe_shipkia_status": {"insert_after": "pe_shipkia_awb_number", "label": "Status"},
        "pe_courier_partner_mode": {"insert_after": "pe_shipkia_status"},
        "pe_shipkia_estimated_delivery": {"insert_after": "pe_courier_partner_mode"},
        "pe_support_ticket_column": {"insert_after": "ndr_support_section", "hidden": 1},
        "pe_support_actions_html": {"insert_after": "pe_support_ticket_column"},
        "ndr_updated_by": {"label": "Handled By"},
        "ndr_note_section": {"insert_after": "ndr_callback_at"},
        "ndr_latest_call": {"hidden": 1},
        "ndr_history_column": {"hidden": 1},
        "ndr_view_history": {"hidden": 1},
    }
    for field, values in changes.items():
        if frappe.db.exists("Custom Field", "Patient Encounter-" + field):
            frappe.db.set_value("Custom Field", "Patient Encounter-" + field, values)
    frappe.clear_cache(doctype="Patient Encounter")
    # This site has an explicit field_order property setter; preserve other tabs.
    import json
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter
    order = [f.fieldname for f in frappe.get_meta("Patient Encounter").fields]
    shipment = [
        "pe_shipment_tracking_section", "pe_shipkia_order_id", "pe_shipkia_stage",
        "pe_delivery_partner", "pe_shipkia_shipment", "ndr_shipment_column",
        "pe_shipkia_awb_number", "pe_shipkia_status", "pe_courier_partner_mode",
        "pe_shipkia_estimated_delivery", "pe_shipkia_delivered_on", "ndr_support_section",
        "pe_support_ticket_column", "pe_support_actions_html", "pe_latest_support_ticket",
        "pe_latest_support_ticket_id", "pe_latest_support_issue_type", "pe_latest_support_stage",
        "pe_latest_support_response", "pe_latest_support_requested_on",
        "ndr_outcome_section", "ndr_tag", "ndr_updated_by", "ndr_outcome_column",
        "ndr_follow_up_at", "ndr_updated_at", "ndr_callback_at", "ndr_note_section",
        "ndr_note", "ndr_call_section", "ndr_actions_html", "ndr_latest_call",
        "ndr_history_column", "ndr_view_history",
    ]
    shipment = [f for f in shipment if f in order]
    remaining = [f for f in order if f not in shipment]
    if "pe_shipment_tracking_tab" in remaining:
        index = remaining.index("pe_shipment_tracking_tab") + 1
        remaining[index:index] = shipment
        make_property_setter("Patient Encounter", None, "field_order",
                             json.dumps(remaining), "Data", for_doctype=True)
    frappe.clear_cache(doctype="Patient Encounter")
