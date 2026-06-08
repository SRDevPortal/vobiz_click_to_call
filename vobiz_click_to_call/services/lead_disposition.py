from __future__ import annotations

from typing import Any

import frappe


SR_LEAD_DISPOSITION = "SR Lead Disposition"
CRM_LEAD = "CRM Lead"
LEAD_DISPOSITION_FIELDS = (
    "sr_lead_disposition",
    "lead_disposition",
    "disposition",
)


def get_lead_disposition_options(
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    lead_status: str | None = None,
) -> list[str]:
    rows = get_lead_disposition_rows(reference_doctype, reference_name, lead_status)
    return [row["name"] for row in rows]


def get_lead_disposition_rows(
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    lead_status: str | None = None,
) -> list[dict[str, Any]]:
    if not frappe.db.exists("DocType", SR_LEAD_DISPOSITION):
        return []

    lead_status = lead_status or _reference_lead_status(reference_doctype, reference_name)
    filters: dict[str, Any] = {"is_active": 1}
    fields = ["name", "sr_disposition_name", "sr_lead_status", "description"]

    if lead_status:
        rows = frappe.get_all(
            SR_LEAD_DISPOSITION,
            filters={**filters, "sr_lead_status": lead_status},
            fields=fields,
            order_by="sr_disposition_name asc",
        )
        return [_normalise_row(row) for row in rows]

    if reference_doctype == CRM_LEAD:
        return []

    rows = frappe.get_all(
        SR_LEAD_DISPOSITION,
        filters=filters,
        fields=fields,
        order_by="sr_lead_status asc, sr_disposition_name asc",
    )
    return [_normalise_row(row) for row in rows]


def get_lead_status_options() -> list[str]:
    if not frappe.db.exists("DocType", "CRM Lead Status"):
        return []
    return frappe.get_all("CRM Lead Status", pluck="name", order_by="name asc")


def get_lead_disposition_context(
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    lead_status: str | None = None,
) -> dict[str, Any]:
    current = get_reference_lead_disposition(reference_doctype, reference_name)
    selected_status = lead_status or current.get("status") or ""
    return {
        **current,
        "status": selected_status,
        "status_options": get_lead_status_options(),
        "options": get_lead_disposition_rows(reference_doctype, reference_name, selected_status),
    }


def get_reference_lead_disposition(reference_doctype: str | None, reference_name: str | None) -> dict[str, Any]:
    if reference_doctype != CRM_LEAD or not reference_name or not frappe.db.exists(CRM_LEAD, reference_name):
        return {"doctype": reference_doctype, "name": reference_name, "status": "", "disposition": "", "fieldname": ""}

    meta = frappe.get_meta(CRM_LEAD)
    fields = ["name"]
    if meta.has_field("status"):
        fields.append("status")
    disposition_field = get_lead_disposition_field(meta)
    if disposition_field:
        fields.append(disposition_field)

    data = frappe.db.get_value(CRM_LEAD, reference_name, fields, as_dict=True) or {}
    return {
        "doctype": CRM_LEAD,
        "name": reference_name,
        "status": data.get("status") or "",
        "disposition": data.get(disposition_field) if disposition_field else "",
        "fieldname": disposition_field or "",
        "status_options": get_lead_status_options(),
        "options": get_lead_disposition_rows(reference_doctype, reference_name),
    }


def sync_call_disposition_to_lead(call_log_doc, disposition: str, lead_status: str | None = None) -> dict[str, Any]:
    if call_log_doc.reference_doctype != CRM_LEAD or not call_log_doc.reference_name:
        return {"synced": False, "reason": "Reference is not CRM Lead."}
    if not frappe.db.exists(CRM_LEAD, call_log_doc.reference_name):
        return {"synced": False, "reason": "CRM Lead not found."}
    if not frappe.db.exists("DocType", SR_LEAD_DISPOSITION):
        return {"synced": False, "reason": "SR Lead Disposition is not installed."}

    disposition = (disposition or "").strip()
    if not disposition:
        return {"synced": False, "reason": "Disposition is empty."}

    filters = {"sr_disposition_name": disposition, "is_active": 1}
    if lead_status:
        filters["sr_lead_status"] = lead_status
    sr_disposition = frappe.db.get_value(SR_LEAD_DISPOSITION, filters, ["name", "sr_disposition_name", "sr_lead_status"], as_dict=True)
    if not sr_disposition:
        return {"synced": False, "reason": "Active SR Lead Disposition not found."}

    lead = frappe.get_doc(CRM_LEAD, call_log_doc.reference_name)
    disposition_field = get_lead_disposition_field(frappe.get_meta(CRM_LEAD))
    status = lead_status or sr_disposition.get("sr_lead_status")
    if status and lead.meta.has_field("status"):
        lead.set("status", status)
    if disposition_field:
        lead.set(disposition_field, sr_disposition.get("sr_disposition_name"))

    lead.save(ignore_permissions=True)
    return {
        "synced": True,
        "lead": lead.name,
        "status": lead.get("status") if lead.meta.has_field("status") else "",
        "disposition": lead.get(disposition_field) if disposition_field else "",
        "disposition_field": disposition_field or "",
    }


def get_lead_disposition_field(meta=None) -> str | None:
    meta = meta or frappe.get_meta(CRM_LEAD)
    for fieldname in LEAD_DISPOSITION_FIELDS:
        if meta.has_field(fieldname):
            return fieldname
    return None


def _reference_lead_status(reference_doctype: str | None, reference_name: str | None) -> str:
    if reference_doctype != CRM_LEAD or not reference_name or not frappe.db.exists(CRM_LEAD, reference_name):
        return ""
    if not frappe.get_meta(CRM_LEAD).has_field("status"):
        return ""
    return frappe.db.get_value(CRM_LEAD, reference_name, "status") or ""


def _normalise_row(row) -> dict[str, Any]:
    name = row.get("sr_disposition_name") or row.get("name")
    return {
        "name": name,
        "doctype_name": row.get("name"),
        "status": row.get("sr_lead_status") or "",
        "description": row.get("description") or "",
    }
