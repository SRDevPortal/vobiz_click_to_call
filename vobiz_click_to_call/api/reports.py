from __future__ import annotations

import frappe
from frappe import _


@frappe.whitelist()
def get_dashboard_summary(from_date: str | None = None, to_date: str | None = None) -> dict:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    from_date = from_date or frappe.utils.today()
    to_date = to_date or frappe.utils.today()
    filters = {
        "creation": ["between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"]],
    }
    if "System Manager" not in frappe.get_roles():
        filters["user"] = frappe.session.user

    rows = frappe.get_all(
        "Vobiz Call Log",
        filters=filters,
        fields=["status", "user", "duration", "billsec", "cost", "disposition", "follow_up_todo"],
    )
    connected_statuses = {"Connected", "Completed"}
    missed_statuses = {"Failed", "Busy", "No Answer", "Cancelled"}

    by_user = {}
    by_status = {}
    by_disposition = {}
    for row in rows:
        by_user[row.user] = by_user.get(row.user, 0) + 1
        by_status[row.status] = by_status.get(row.status, 0) + 1
        if row.disposition:
            by_disposition[row.disposition] = by_disposition.get(row.disposition, 0) + 1

    return {
        "total_calls": len(rows),
        "connected_calls": len([row for row in rows if row.status in connected_statuses]),
        "missed_calls": len([row for row in rows if row.status in missed_statuses]),
        "average_duration": round(sum(row.duration or 0 for row in rows) / len(rows), 2) if rows else 0,
        "total_talk_time": sum(row.billsec or row.duration or 0 for row in rows),
        "total_cost": sum(row.cost or 0 for row in rows),
        "followups_created": len([row for row in rows if row.follow_up_todo]),
        "by_user": by_user,
        "by_status": by_status,
        "by_disposition": by_disposition,
    }
