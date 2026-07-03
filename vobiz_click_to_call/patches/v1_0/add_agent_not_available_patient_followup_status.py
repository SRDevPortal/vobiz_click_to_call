import frappe


def execute():
    if not frappe.db.exists("DocType", "Patient"):
        return

    field = frappe.get_meta("Patient").get_field("sr_followup_status")
    if not field:
        return

    options = [row.strip() for row in str(field.options or "").splitlines() if row.strip()]
    if "Agent Not Available" in options:
        return

    options.append("Agent Not Available")

    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    make_property_setter(
        "Patient",
        "sr_followup_status",
        "options",
        "\n".join(options),
        "Text",
        validate_fields_for_doctype=False,
    )
    frappe.clear_cache(doctype="Patient")
