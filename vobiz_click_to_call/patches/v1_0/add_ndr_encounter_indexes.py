"""Index NDR eligibility and newest-created pagination on large encounter tables."""
import frappe


def execute():
    if not frappe.db.table_exists("Patient Encounter"):
        return
    required = ("pe_shipkia_stage", "pe_shipkia_status")
    if any(not frappe.db.has_column("Patient Encounter", field) for field in required):
        return
    existing = {row[2] for row in frappe.db.sql("SHOW INDEX FROM `tabPatient Encounter`")}
    definitions = {
        "idx_pe_ndr_stage_status": "`pe_shipkia_stage`(140), `pe_shipkia_status`",
        "idx_pe_ndr_created": "`creation`, `modified`, `name`",
    }
    additions = [f"ADD INDEX `{name}` ({columns})"
                 for name, columns in definitions.items() if name not in existing]
    if additions:
        frappe.db.sql("ALTER TABLE `tabPatient Encounter` " + ", ".join(additions)
                      + ", ALGORITHM=INPLACE, LOCK=NONE")
