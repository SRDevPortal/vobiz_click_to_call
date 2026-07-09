from __future__ import annotations

from typing import Any

import frappe


SR_LEAD_DISPOSITION = "SR Lead Disposition"
CRM_LEAD_STATUS = "CRM Lead Status"
CRM_LEAD = "CRM Lead"
PATIENT = "Patient"
PATIENT_FOLLOWUP_STATUS_FIELDS = (
    "sr_followup_status",
    "followup_status",
    "follow_up_status",
)
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
    if not frappe.db.exists("DocType", CRM_LEAD):
        return []

    meta = frappe.get_meta(CRM_LEAD)
    status_field = meta.get_field("status")
    if not status_field:
        return []

    linked_doctype = (status_field.options or "").strip() if (status_field.fieldtype or "").lower() == "link" else ""
    if linked_doctype and frappe.db.exists("DocType", linked_doctype):
        linked_meta = frappe.get_meta(linked_doctype)
        filters: dict[str, Any] = {}
        if linked_meta.has_field("is_active"):
            filters["is_active"] = 1
        return frappe.get_all(
            linked_doctype,
            filters=filters,
            pluck="name",
            order_by="sort_order asc, name asc" if linked_meta.has_field("sort_order") else "name asc",
        )

    if frappe.db.exists("DocType", CRM_LEAD_STATUS):
        status_meta = frappe.get_meta(CRM_LEAD_STATUS)
        filters: dict[str, Any] = {}
        if status_meta.has_field("is_active"):
            filters["is_active"] = 1
        return frappe.get_all(
            CRM_LEAD_STATUS,
            filters=filters,
            pluck="name",
            order_by="sort_order asc, name asc" if status_meta.has_field("sort_order") else "name asc",
        )

    if not getattr(status_field, "options", None):
        return []
    return [option.strip() for option in str(status_field.options).splitlines() if option.strip()]


def get_patient_followup_status_field_options() -> list[str]:
    if not frappe.db.exists("DocType", PATIENT):
        return []

    meta = frappe.get_meta(PATIENT)
    field = None
    for fieldname in PATIENT_FOLLOWUP_STATUS_FIELDS:
        field = meta.get_field(fieldname)
        if field:
            break
    if not field:
        for candidate in meta.fields:
            label = (candidate.label or "").strip().lower().replace("-", " ")
            if label == "followup status":
                field = candidate
                break

    if not field or not getattr(field, "options", None):
        return []
    if (field.fieldtype or "").lower() == "link":
        return []

    return [option.strip() for option in str(field.options).splitlines() if option.strip()]


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
        if not lead_status:
            return {"synced": False, "reason": "Disposition and lead status are empty."}
        lead = frappe.get_doc(CRM_LEAD, call_log_doc.reference_name)
        if lead.meta.has_field("status"):
            frappe.db.set_value(CRM_LEAD, lead.name, "status", lead_status, update_modified=True)
            lead.set("status", lead_status)
            return {
                "synced": True,
                "lead": lead.name,
                "status": lead.get("status"),
                "disposition": "",
                "disposition_field": get_lead_disposition_field(frappe.get_meta(CRM_LEAD)) or "",
            }
        return {"synced": False, "reason": "CRM Lead status field not found."}

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
        frappe.db.set_value(CRM_LEAD, lead.name, "status", status, update_modified=True)
        lead.set("status", status)
    if disposition_field:
        frappe.db.set_value(CRM_LEAD, lead.name, disposition_field, sr_disposition.get("sr_disposition_name"), update_modified=True)
        lead.set(disposition_field, sr_disposition.get("sr_disposition_name"))
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
