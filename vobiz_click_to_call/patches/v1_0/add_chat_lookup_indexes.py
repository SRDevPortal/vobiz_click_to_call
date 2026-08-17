from __future__ import annotations

import frappe


INDEXES = {
    "Chat Conversation": {
        "idx_chat_conversation_reference_modified": (
            "linked_reference_doctype",
            "linked_reference_name",
            "modified",
        ),
        "idx_chat_conversation_crm_lead_modified": ("linked_crm_lead", "modified"),
        "idx_chat_conversation_contact_modified": ("contact", "modified"),
    },
    "Chat Contact": {
        "idx_chat_contact_phone_number": ("phone_number",),
    },
}


def execute():
    for doctype, requested_indexes in INDEXES.items():
        if not frappe.db.table_exists(doctype):
            continue
        columns = set(frappe.db.get_table_columns(doctype))
        existing = _existing_index_columns(f"tab{doctype}")
        additions = []
        for index_name, index_columns in requested_indexes.items():
            if not set(index_columns).issubset(columns):
                continue
            if any(existing_columns[: len(index_columns)] == index_columns for existing_columns in existing.values()):
                continue
            column_sql = ", ".join(f"`{column}`" for column in index_columns)
            additions.append(f"add index `{index_name}` ({column_sql})")
        if additions:
            frappe.db.sql(
                f"""
                alter table `tab{doctype}`
                {", ".join(additions)},
                algorithm=inplace, lock=none
                """
            )


def _existing_index_columns(table: str) -> dict[str, tuple[str, ...]]:
    rows = frappe.db.sql(
        """
        select index_name, column_name
        from information_schema.statistics
        where table_schema = database() and table_name = %s
        order by index_name, seq_in_index
        """,
        table,
        as_dict=True,
    )
    indexes: dict[str, list[str]] = {}
    for row in rows:
        indexes.setdefault(row.index_name, []).append(row.column_name)
    return {name: tuple(columns) for name, columns in indexes.items()}
