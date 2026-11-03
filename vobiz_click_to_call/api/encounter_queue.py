"""Permission-aware Patient Encounter queue for the agent console."""
from __future__ import annotations

import json
import frappe
from frappe import _
from frappe.model import get_permitted_fields
from vobiz_click_to_call.services.queue_sources import queue_includes

DOCTYPE = "Patient Encounter"
SCALAR_TYPES = {"Data", "Link", "Dynamic Link", "Select", "Date", "Datetime", "Time",
                "Int", "Float", "Currency", "Percent", "Check", "Small Text", "Text"}
OPERATORS = {"=", "!=", "in", "not in", "is", ">", "<", ">=", "<=", "like", "not like", "between", "timespan"}
DEFAULT_FIELDS = ["name", "patient", "patient_name", "encounter_date", "status"]
NDR_FIELDS = ("pe_shipkia_stage", "pe_shipkia_status")


def _view(value):
    if value not in ("encounters", "ndr"):
        frappe.throw(_("Invalid encounter queue view."))
    return value


def _ndr_values(settings, field):
    values = _array(settings.get(field), [])
    if not values or len(values) > 100 or any(not isinstance(v, str) or not v.strip() for v in values):
        frappe.throw(_("Select between 1 and 100 non-empty values for each NDR stage/status setting."))
    return list(dict.fromkeys(values))


def field_catalog():
    meta = frappe.get_meta(DOCTYPE)
    fields = {field.fieldname: {"fieldname": field.fieldname, "label": field.label or field.fieldname,
              "fieldtype": field.fieldtype, "options": field.options}
              for field in meta.fields if field.fieldtype in SCALAR_TYPES and not field.hidden and not field.get("is_virtual")}
    for name, label, kind in [("name", "Encounter ID", "Data"), ("creation", "Created On", "Datetime"),
                              ("modified", "Updated On", "Datetime"), ("docstatus", "Document Status", "Int")]:
        fields[name] = dict(fieldname=name, label=label, fieldtype=kind)
    return fields


def _array(value, default):
    if not value:
        return default
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        frappe.throw(_("Encounter queue configuration must be valid JSON."))
    if not isinstance(result, list):
        frappe.throw(_("Encounter queue configuration must be a JSON array."))
    return result


def configuration(settings, view="encounters"):
    _view(view)
    catalog = field_catalog()
    columns = _array(settings.get("encounter_queue_fields"), DEFAULT_FIELDS)
    if not columns or len(columns) > 20 or any(not isinstance(f, str) or f not in catalog for f in columns):
        frappe.throw(_("Choose between 1 and 20 valid Patient Encounter columns."))
    filters = _array(settings.get("encounter_queue_filters"), [])
    if len(filters) > 20:
        frappe.throw(_("Use at most 20 encounter queue filters."))
    normalized = []
    for item in filters:
        if not isinstance(item, (list, tuple)) or len(item) not in (3, 4, 5):
            frappe.throw(_("Each encounter filter must contain a field, operator and value."))
        if len(item) in (4, 5):
            if item[0] != DOCTYPE:
                frappe.throw(_("Filters must belong to Patient Encounter."))
            item = item[1:4]
        field, operator, value = item
        if isinstance(operator, str):
            operator = operator.lower()
        if not isinstance(field, str) or field not in catalog or not isinstance(operator, str) or operator not in OPERATORS:
            frappe.throw(_("Invalid encounter queue filter field or operator."))
        if operator == "is" and value not in ("set", "not set"):
            frappe.throw(_("Use set or not set for an Is filter."))
        if isinstance(value, dict) or (isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value)):
            frappe.throw(_("Invalid encounter queue filter value."))
        normalized.append([DOCTYPE, field, operator, value])
    columns = list(dict.fromkeys(columns))
    if view == "ndr":
        if any(field not in catalog for field in NDR_FIELDS):
            frappe.throw(_("Install the Patient Encounter Shipkia Stage and Status fields before enabling NDR."))
        remaining = [field for field in columns if field not in NDR_FIELDS]
        for field in ("name", "patient"):
            if len(remaining) < 2 and field not in remaining:
                remaining.append(field)
        columns = remaining[:2] + list(NDR_FIELDS) + remaining[2:]
        for field, setting in zip(NDR_FIELDS, ("ndr_shipkia_stages", "ndr_shipkia_statuses")):
            normalized.append([DOCTYPE, field, "in", _ndr_values(settings, setting)])
    return columns, normalized, catalog


def validate_configuration(settings):
    if not settings.get("enable_encounter_queue") and not settings.get("enable_ndr_queue"):
        return
    if not frappe.db.exists("DocType", DOCTYPE):
        frappe.throw(_("Patient Encounter must be installed to enable its queue."))
    configuration(settings)
    if settings.get("enable_ndr_queue"):
        configuration(settings, "ndr")


def _settings(view="encounters"):
    _view(view)
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."), frappe.PermissionError)
    settings = frappe.get_single("Vobiz Settings")
    if not settings.get("enable_ndr_queue" if view == "ndr" else "enable_encounter_queue"):
        frappe.throw(_("NDR Queue is disabled.") if view == "ndr" else _("Patient Encounter Queue is disabled."))
    if view == "ndr" and not has_ndr_mapping():
        frappe.throw(_("NDR must be selected in your enabled Vobiz User Mapping."), frappe.PermissionError)
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw(_("Not permitted to read Patient Encounters."), frappe.PermissionError)
    return settings


def has_ndr_mapping():
    if frappe.session.user == "Guest":
        return False
    source = frappe.db.get_value("Vobiz User Mapping", {"user": frappe.session.user, "enabled": 1}, "queue_source")
    return queue_includes(source, "NDR")


@frappe.whitelist()
def get_configuration_fields():
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only System Managers can configure this queue."), frappe.PermissionError)
    return list(field_catalog().values())


@frappe.whitelist()
def get_available_views():
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required."), frappe.PermissionError)
    settings = frappe.get_single("Vobiz Settings")
    return {"ndr": bool(settings.get("enable_ndr_queue") and has_ndr_mapping()
                        and frappe.has_permission(DOCTYPE, "read"))}


@frappe.whitelist()
def get_ndr_options(field, search=None):
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only System Managers can configure this queue."), frappe.PermissionError)
    if field not in NDR_FIELDS or field not in field_catalog():
        frappe.throw(_("Invalid or missing Shipkia field."))
    if field not in get_permitted_fields(DOCTYPE, permission_type="read"):
        frappe.throw(_("Not permitted to read this Shipkia field."), frappe.PermissionError)
    filters = [[DOCTYPE, field, "is", "set"]]
    if search:
        filters.append([DOCTYPE, field, "like", "%" + str(search).strip()[:140] + "%"])
    rows = frappe.get_list(DOCTYPE, fields=[field], filters=filters,
                          group_by=field, order_by=field + " asc", page_length=100)
    return [row[field] for row in rows if row.get(field) and row[field].strip()]


@frappe.whitelist()
def get_queue(limit=25, limit_start=0, search=None, view="encounters", sort_by=None, sort_order="asc"):
    settings = _settings(view)
    columns, filters, catalog = configuration(settings, view)
    permitted = set(get_permitted_fields(DOCTYPE, permission_type="read"))
    columns = [field for field in columns if field in permitted]
    if any(item[1] not in permitted for item in filters):
        frappe.throw(_("You do not have access to the configured encounter filter fields."), frappe.PermissionError)
    order_by = "modified desc, name desc"
    if sort_by:
        if view != "ndr" or sort_by not in NDR_FIELDS or sort_order not in ("asc", "desc"):
            frappe.throw(_("Invalid NDR sort field or direction."))
        order_by = f"{sort_by} {sort_order}, modified desc, name desc"
    limit = max(1, min(frappe.utils.cint(limit) or 25, 100))
    limit_start = max(0, frappe.utils.cint(limit_start))
    search = str(search or "").strip()[:140]
    search_filters = []
    if search:
        search_filters = [[DOCTYPE, field, "like", "%" + search + "%"]
                          for field in ("name", "patient", "patient_name") if field in permitted]
    rows = frappe.get_list(
        DOCTYPE, fields=list(dict.fromkeys(["name"] + columns)),
        filters=filters, or_filters=search_filters,
        order_by=order_by, limit_start=limit_start, page_length=limit + 1,
    )
    return {"columns": [catalog[field] for field in columns], "rows": rows[:limit],
            "has_more": len(rows) > limit}


@frappe.whitelist()
def get_encounter_context(encounter, view="encounters"):
    settings = _settings(view)
    columns, filters, catalog = configuration(settings, view)
    permitted = set(get_permitted_fields(DOCTYPE, permission_type="read"))
    if any(item[1] not in permitted for item in filters):
        frappe.throw(_("You do not have access to the configured encounter filter fields."), frappe.PermissionError)
    # Recheck both saved queue restrictions and record permissions when opening details.
    visible = frappe.get_list(DOCTYPE, filters=filters + [[DOCTYPE, "name", "=", encounter]],
                              fields=["name"], page_length=1)
    if not visible:
        frappe.throw(_("This encounter is no longer available in your queue."), frappe.PermissionError)
    doc = frappe.get_doc(DOCTYPE, encounter)
    doc.check_permission("read")
    if not doc.patient:
        frappe.throw(_("This encounter has no linked patient."))
    from vobiz_click_to_call.api.console import get_reference_context
    context = get_reference_context("Patient", doc.patient, lite=1)
    context["encounter"] = {"name": doc.name}
    return context
