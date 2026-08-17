from __future__ import annotations

import frappe


def cleanup_issue_call_log_links(doc, method=None) -> None:
    """Break Issue <-> Vobiz Call Log links before Frappe validates delete links."""
    if not doc or not doc.name or not frappe.db.exists("DocType", "Vobiz Call Log"):
        return
    _clear_mappings_for_issue_call_logs(doc.name)
    if _has_field("Vobiz Call Log", "issue"):
        frappe.db.sql(
            "update `tabVobiz Call Log` set `issue` = '' where `issue` = %s",
            doc.name,
        )
    if _has_field("Vobiz Call Log", "reference_doctype") and _has_field("Vobiz Call Log", "reference_name"):
        frappe.db.sql(
            """
            update `tabVobiz Call Log`
            set `reference_doctype` = '', `reference_name` = ''
            where `reference_doctype` = 'Issue' and `reference_name` = %s
            """,
            doc.name,
        )


def cleanup_call_log_reverse_links(doc, method=None) -> None:
    if not doc or not doc.name:
        return

    _clear_issue_fields_linking_call_log(doc.name)
    _clear_mapping_current_call(doc.name)

    if getattr(doc, "reference_doctype", None) == "Issue":
        doc.reference_doctype = ""
        doc.reference_name = ""
    if getattr(doc, "issue", None):
        doc.issue = ""


def _clear_mappings_for_issue_call_logs(issue_name: str) -> None:
    if not frappe.db.exists("DocType", "Vobiz User Mapping"):
        return
    if _has_field("Vobiz Call Log", "issue"):
        frappe.db.sql(
            """
            update `tabVobiz User Mapping` mapping
            inner join `tabVobiz Call Log` call_log on call_log.`name` = mapping.`current_call_log`
            set mapping.`current_call_log` = ''
            where call_log.`issue` = %s
            """,
            issue_name,
        )
    if _has_field("Vobiz Call Log", "reference_doctype") and _has_field("Vobiz Call Log", "reference_name"):
        frappe.db.sql(
            """
            update `tabVobiz User Mapping` mapping
            inner join `tabVobiz Call Log` call_log on call_log.`name` = mapping.`current_call_log`
            set mapping.`current_call_log` = ''
            where call_log.`reference_doctype` = 'Issue' and call_log.`reference_name` = %s
            """,
            issue_name,
        )


def _clear_issue_fields_linking_call_log(call_log: str) -> None:
    if not frappe.db.exists("DocType", "Issue"):
        return

    meta = frappe.get_meta("Issue")
    link_fields = [
        df.fieldname
        for df in meta.fields
        if df.fieldtype == "Link" and df.options == "Vobiz Call Log"
    ]
    dynamic_fields = [
        (df.fieldname, df.options)
        for df in meta.fields
        if df.fieldtype == "Dynamic Link" and df.options
    ]

    for fieldname in link_fields:
        frappe.db.sql(f"update `tabIssue` set `{fieldname}` = '' where `{fieldname}` = %s", call_log)

    for fieldname, doctype_field in dynamic_fields:
        if not meta.has_field(doctype_field):
            continue
        frappe.db.sql(
            f"""
            update `tabIssue`
            set `{doctype_field}` = '', `{fieldname}` = ''
            where `{doctype_field}` = 'Vobiz Call Log' and `{fieldname}` = %s
            """,
            call_log,
        )


def _clear_mapping_current_call(call_log: str) -> None:
    if not frappe.db.exists("DocType", "Vobiz User Mapping"):
        return

    frappe.db.sql(
        "update `tabVobiz User Mapping` set `current_call_log` = '' where `current_call_log` = %s",
        call_log,
    )


def _has_field(doctype: str, fieldname: str) -> bool:
    return frappe.db.exists("DocType", doctype) and frappe.get_meta(doctype).has_field(fieldname)
