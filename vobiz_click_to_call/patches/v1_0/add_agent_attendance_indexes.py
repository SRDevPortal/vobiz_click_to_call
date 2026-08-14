from __future__ import annotations

import frappe


DOCTYPE = "Vobiz Agent Attendance Log"
TABLE = f"tab{DOCTYPE}"
INDEXES = {
    "idx_vobiz_attendance_open_session": (
        "`agent_user`, `tab_id`, `shift_date`, `status`, `creation`"
    ),
    "idx_vobiz_attendance_daily_source": (
        "`agent_user`, `shift_date`, `source`, `online_from`"
    ),
    "idx_vobiz_attendance_stale": "`status`, `source`, `last_seen_at`",
}


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return

    existing = {
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
    additions = []
    for index_name, columns in INDEXES.items():
        if index_name in existing:
            continue
        additions.append(f"add index `{index_name}` ({columns})")

    if additions:
        frappe.db.sql(
            f"""
            alter table `{TABLE}`
            {", ".join(additions)},
            algorithm=inplace, lock=none
            """
        )
