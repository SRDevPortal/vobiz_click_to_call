from __future__ import annotations

import frappe


DOCTYPE = "Vobiz Call Log"
TABLE = f"tab{DOCTYPE}"
INDEXES = {
    "idx_vobiz_call_creation": ("creation",),
    "idx_vobiz_call_cdr_status_creation": ("cdr_sync_status", "creation"),
    "idx_vobiz_call_reference_creation": (
        "reference_doctype",
        "reference_name",
        "creation",
    ),
    "idx_vobiz_call_reference_activity": (
        "reference_doctype",
        "reference_name",
        "direction",
        "status",
        "creation",
    ),
    "idx_vobiz_call_reference_type_creation": ("reference_doctype", "creation"),
    "idx_vobiz_call_user_creation": ("user", "creation"),
    "idx_vobiz_call_normalized_customer_creation": (
        "normalized_customer_number",
        "creation",
    ),
    "idx_vobiz_call_customer_creation": ("customer_number", "creation"),
    "idx_vobiz_call_uuid": ("call_uuid",),
    "idx_vobiz_call_request_uuid": ("request_uuid",),
    "idx_vobiz_call_recording_id": ("recording_id",),
    "idx_vobiz_call_transcription_id": ("transcription_id",),
    "idx_vobiz_call_sip_call_id": ("sip_call_id",),
}


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return

    existing_indexes = {
        row[0]
        for row in frappe.db.sql(
            """
            select distinct index_name
            from information_schema.statistics
            where table_schema = database() and table_name = %s
            """,
            TABLE,
        )
    }
    table_columns = set(frappe.db.get_table_columns(DOCTYPE))

    additions = []
    for index_name, columns in INDEXES.items():
        if index_name in existing_indexes or not set(columns).issubset(table_columns):
            continue
        column_sql = ", ".join(f"`{column}`" for column in columns)
        additions.append(f"add index `{index_name}` ({column_sql})")

    if additions:
        frappe.db.sql(
            f"""
            alter table `{TABLE}`
            {", ".join(additions)},
            algorithm=inplace, lock=none
            """
        )

