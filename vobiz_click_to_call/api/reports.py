from __future__ import annotations

import hashlib

import frappe
from frappe import _

from vobiz_click_to_call.api.console import (
    _analytics_bucket_sql,
    _analytics_date_range,
    _analytics_talk_sql,
)


REPORT_CACHE_SECONDS = 30


@frappe.whitelist()
def get_dashboard_summary(from_date: str | None = None, to_date: str | None = None) -> dict:
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."))

    from_date, to_date = _analytics_date_range(from_date, to_date)
    cache_payload = f"{frappe.session.user}|{from_date}|{to_date}"
    cache_key = f"vobiz_dashboard_summary:{hashlib.sha256(cache_payload.encode()).hexdigest()}"
    try:
        cached = frappe.cache().get_value(cache_key)
        if cached is not None:
            return cached
    except Exception:
        pass

    params = {
        "start": f"{from_date} 00:00:00",
        "end": f"{to_date} 23:59:59",
    }
    conditions = ["`creation` between %(start)s and %(end)s"]
    if "System Manager" not in frappe.get_roles():
        params["user"] = frappe.session.user
        conditions.append("`user` = %(user)s")
    where_sql = " and ".join(conditions)
    bucket_sql = _analytics_bucket_sql()
    talk_sql = _analytics_talk_sql()

    summary_rows = frappe.db.sql(
        f"""
        select count(*) as total_calls,
               sum(bucket = 'connected') as connected_calls,
               sum(bucket in ('missed', 'busy', 'no_answer', 'failed', 'cancelled')) as missed_calls,
               avg(coalesce(`duration`, 0)) as average_duration,
               sum(talk_seconds) as total_talk_time,
               sum(coalesce(`cost`, 0)) as total_cost,
               sum(case when `follow_up_todo` is not null and `follow_up_todo` != '' then 1 else 0 end) as followups_created
        from (
            select *, {bucket_sql} as bucket, {talk_sql} as talk_seconds
            from `tabVobiz Call Log`
            where {where_sql}
        ) analytics
        """,
        params,
        as_dict=True,
    )
    summary = summary_rows[0] if summary_rows else {}
    grouped = {}
    for fieldname in ("user", "status", "disposition"):
        grouped[fieldname] = {
            (row.get(fieldname) or _("Unassigned")): frappe.utils.cint(row.call_count)
            for row in frappe.db.sql(
                f"""
                select `{fieldname}`, count(*) as call_count
                from `tabVobiz Call Log`
                where {where_sql}
                group by `{fieldname}`
                """,
                params,
                as_dict=True,
            )
            if row.get(fieldname)
        }

    result = {
        "total_calls": frappe.utils.cint(summary.get("total_calls")),
        "connected_calls": frappe.utils.cint(summary.get("connected_calls")),
        "missed_calls": frappe.utils.cint(summary.get("missed_calls")),
        "average_duration": round(frappe.utils.flt(summary.get("average_duration")), 2),
        "total_talk_time": frappe.utils.cint(summary.get("total_talk_time")),
        "total_cost": frappe.utils.flt(summary.get("total_cost")),
        "followups_created": frappe.utils.cint(summary.get("followups_created")),
        "by_user": grouped["user"],
        "by_status": grouped["status"],
        "by_disposition": grouped["disposition"],
    }
    try:
        frappe.cache().set_value(cache_key, result, expires_in_sec=REPORT_CACHE_SECONDS)
    except Exception:
        pass
    return result
