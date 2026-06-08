from __future__ import annotations

import frappe


def cleanup_issue_call_log_links(doc, method=None) -> None:
    """Break Issue <-> Vobiz Call Log links before Frappe validates delete links."""
    if not doc or not doc.name or not frappe.db.exists("DocType", "Vobiz Call Log"):
        return

    for call_log in _call_logs_for_issue(doc.name):
        _clear_call_log_issue_links(call_log, doc.name)
        _clear_mapping_current_call(call_log)


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


def _call_logs_for_issue(issue_name: str) -> set[str]:
    names: set[str] = set()

    if _has_field("Vobiz Call Log", "issue"):
        names.update(
            frappe.get_all(
                "Vobiz Call Log",
                filters={"issue": issue_name},
                pluck="name",
                ignore_permissions=True,
            )
        )

    if _has_field("Vobiz Call Log", "reference_doctype") and _has_field("Vobiz Call Log", "reference_name"):
        names.update(
            frappe.get_all(
                "Vobiz Call Log",
                filters={"reference_doctype": "Issue", "reference_name": issue_name},
                pluck="name",
                ignore_permissions=True,
            )
        )

    return names


def _clear_call_log_issue_links(call_log: str, issue_name: str) -> None:
    values = {}
    if _has_field("Vobiz Call Log", "issue"):
        values["issue"] = ""

    if (
        _has_field("Vobiz Call Log", "reference_doctype")
        and _has_field("Vobiz Call Log", "reference_name")
        and frappe.db.get_value("Vobiz Call Log", call_log, "reference_doctype") == "Issue"
        and frappe.db.get_value("Vobiz Call Log", call_log, "reference_name") == issue_name
    ):
        values.update({"reference_doctype": "", "reference_name": ""})

    if values:
        frappe.db.set_value("Vobiz Call Log", call_log, values, update_modified=False)


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
        for issue in frappe.get_all(
            "Issue",
            filters={fieldname: call_log},
            pluck="name",
            ignore_permissions=True,
        ):
            frappe.db.set_value("Issue", issue, fieldname, "", update_modified=False)

    for fieldname, doctype_field in dynamic_fields:
        if not meta.has_field(doctype_field):
            continue
        for issue in frappe.get_all(
            "Issue",
            filters={doctype_field: "Vobiz Call Log", fieldname: call_log},
            pluck="name",
            ignore_permissions=True,
        ):
            frappe.db.set_value(
                "Issue",
                issue,
                {doctype_field: "", fieldname: ""},
                update_modified=False,
            )


def _clear_mapping_current_call(call_log: str) -> None:
    if not frappe.db.exists("DocType", "Vobiz User Mapping"):
        return

    for mapping in frappe.get_all(
        "Vobiz User Mapping",
        filters={"current_call_log": call_log},
        pluck="name",
        ignore_permissions=True,
    ):
        frappe.db.set_value("Vobiz User Mapping", mapping, "current_call_log", "", update_modified=False)


def _has_field(doctype: str, fieldname: str) -> bool:
    return frappe.db.exists("DocType", doctype) and frappe.get_meta(doctype).has_field(fieldname)
