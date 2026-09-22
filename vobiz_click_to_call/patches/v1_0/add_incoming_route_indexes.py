"""Make exact callback identity lookups cheap, including on already-migrated sites."""
import frappe

FIELDS = ("call_uuid", "sip_call_id", "request_id", "request_uuid")


def execute():
    if not frappe.db.table_exists("Vobiz Call Log"):
        return
    columns = set(frappe.db.get_table_columns("Vobiz Call Log"))
    indexed = {row[0] for row in frappe.db.sql(
        "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabVobiz Call Log' AND SEQ_IN_INDEX=1"
    )}
    additions = [
        f"ADD INDEX `vobiz_incoming_{field}` (`{field}`)"
        for field in FIELDS if field in columns and field not in indexed
    ]
    if additions:
        frappe.db.sql("ALTER TABLE `tabVobiz Call Log` " + ", ".join(additions)
                      + ", ALGORITHM=INPLACE, LOCK=NONE")
