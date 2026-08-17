from __future__ import annotations

import csv
import json
import sys
import time
from urllib.parse import quote

import frappe
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_REMOTE = "https://erps.eternityecommerce.com"
DEFAULT_KEY_FILE = "/mnt/c/Users/Amit/Downloads/frappe_api_keys_jagmohan_eternity.csv"
DEFAULT_SNAPSHOT_FILE = "/home/mit/frappe-bench/sites/site1.local/private/files/vobiz_analytics_snapshot.json"
DEFAULT_REMOTE_PATIENT_FILE = "/home/mit/frappe-bench/sites/site1.local/private/files/remote_patient_followup.json"
DEFAULT_REMOTE_PATIENT_DOCTYPE_FILE = "/home/mit/frappe-bench/sites/site1.local/private/files/remote_patient_doctype.json"
DEFAULT_REMOTE_SR_FOLLOWUP_STATUS_FILE = "/home/mit/frappe-bench/sites/site1.local/private/files/remote_sr_followup_status.json"
DEFAULT_REMOTE_SR_FOLLOWUP_STATUS_DOCTYPE_FILE = "/home/mit/frappe-bench/sites/site1.local/private/files/remote_sr_followup_status_doctype.json"
ANALYTICS_DOCTYPES = (
    "Team",
    "Team User",
    "CRM Lead",
    "Patient",
    "Vobiz User Mapping",
    "Vobiz Call Log",
)
SYSTEM_FIELDS = {
    "doctype",
    "idx",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
}


def run(
    remote: str = DEFAULT_REMOTE,
    key_file: str = DEFAULT_KEY_FILE,
    page_length: int | str = 100,
    dry_run: int | str = 0,
    doctypes: list[str] | str | None = None,
) -> list[dict]:
    api_key, api_secret = read_token(key_file)
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"token {api_key}:{api_secret}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 VobizAnalyticsLocalImport/1.0",
        }
    )
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))

    selected_doctypes = parse_doctypes(doctypes)
    summary = []
    for doctype in selected_doctypes:
        summary.append(
            import_doctype(
                session,
                remote,
                doctype,
                page_length=frappe.utils.cint(page_length) or 100,
                dry_run=bool(frappe.utils.cint(dry_run)),
            )
        )
    return summary


def import_from_file(
    snapshot_file: str = DEFAULT_SNAPSHOT_FILE,
    dry_run: int | str = 0,
    doctypes: list[str] | str | None = None,
) -> list[dict]:
    with open(snapshot_file, encoding="utf-8-sig") as handle:
        snapshot = json.load(handle)

    selected_doctypes = parse_doctypes(doctypes)
    dry = bool(frappe.utils.cint(dry_run))
    summary = []
    for doctype in selected_doctypes:
        docs = snapshot.get(doctype) or []
        result = {"doctype": doctype, "remote": len(docs), "inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
        if not frappe.db.exists("DocType", doctype):
            result.update({"skipped": len(docs), "reason": "missing_local_doctype"})
            summary.append(result)
            continue
        for index, doc_data in enumerate(docs, start=1):
            for attempt in range(3):
                try:
                    action = upsert_doc(doc_data, doctype, dry_run=dry)
                    result[action] = result.get(action, 0) + 1
                    if index % 100 == 0:
                        frappe.db.commit()
                        print(f"{doctype}: {index}/{len(docs)}")
                    break
                except Exception as exc:
                    frappe.db.rollback()
                    if "Deadlock found" in str(exc) and attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    result["errors"] += 1
                    print(f"{doctype}: failed {doc_data.get('name')}: {exc}", file=sys.stderr)
                    break
        if not dry:
            frappe.db.commit()
        summary.append(result)
    return summary


def snapshot_counts() -> dict[str, int]:
    return {
        doctype: frappe.db.count(doctype)
        for doctype in ANALYTICS_DOCTYPES
        if frappe.db.exists("DocType", doctype)
    }


def call_log_date_range() -> dict[str, object]:
    row = frappe.db.sql(
        """
        select min(creation), max(creation), count(*)
        from `tabVobiz Call Log`
        """,
        as_list=True,
    )[0]
    return {"first_call": row[0], "last_call": row[1], "rows": row[2]}


def analytics_smoke_test(
    from_date: str = "2026-06-24",
    to_date: str = "2026-07-06",
    queue_source: str = "CRM Lead",
) -> dict[str, object]:
    from vobiz_click_to_call.api.console import get_analytics

    data = get_analytics(
        from_date=from_date,
        to_date=to_date,
        queue_source=queue_source,
        include_calls=1,
        call_limit=5,
    )
    return {
        "summary": data.get("summary"),
        "agent_rows": len(data.get("agents") or []),
        "call_rows": len(data.get("calls") or []),
        "lead_owner_options": len(data.get("lead_owner_options") or []),
        "team_options": len(data.get("team_options") or []),
        "department_options": len(data.get("department_options") or []),
    }


def import_user_mappings_from_file() -> list[dict]:
    return import_from_file(doctypes=["Vobiz User Mapping"])


def sync_patient_followup_status_from_file(
    local_patient: str = "HLC-PAT-2026-00001",
    remote_patient_file: str = DEFAULT_REMOTE_PATIENT_FILE,
) -> dict[str, object]:
    with open(remote_patient_file, encoding="utf-8-sig") as handle:
        remote_data = json.load(handle)
    remote_doc_data = remote_data.get("data") or remote_data
    fieldname = patient_followup_status_field()
    value = remote_doc_data.get(fieldname)
    if value in (None, ""):
        raise ValueError(f"Remote Patient does not contain a value for {fieldname}.")
    if not frappe.db.exists("Patient", local_patient):
        raise ValueError(f"Local Patient not found: {local_patient}")
    old_value = frappe.db.get_value("Patient", local_patient, fieldname)
    frappe.db.set_value("Patient", local_patient, fieldname, value, update_modified=False)
    frappe.db.commit()
    return {
        "patient": local_patient,
        "fieldname": fieldname,
        "old_value": old_value,
        "new_value": value,
    }


def patient_followup_status_field() -> str:
    meta = frappe.get_meta("Patient")
    for field in meta.fields:
        if (field.label or "").strip().lower() == "followup status":
            return field.fieldname
    for field in meta.fields:
        if field.fieldname in {"followup_status", "follow_up_status"}:
            return field.fieldname
    raise ValueError("Could not find Followup Status field on Patient.")


def patient_followup_status_info() -> dict[str, object]:
    fieldname = patient_followup_status_field()
    meta = frappe.get_meta("Patient")
    field = meta.get_field(fieldname)
    custom_field = frappe.db.get_value(
        "Custom Field",
        {"dt": "Patient", "fieldname": fieldname},
        ["name", "fieldtype", "options"],
        as_dict=True,
    )
    return {
        "fieldname": fieldname,
        "label": field.label,
        "fieldtype": field.fieldtype,
        "options": field.options,
        "custom_field": custom_field,
    }


def sr_followup_status_link_info() -> dict[str, object]:
    return {
        "patient_field": patient_followup_status_info(),
        "doctype_exists": bool(frappe.db.exists("DocType", "SR Followup Status")),
        "status_count": frappe.db.count("SR Followup Status") if frappe.db.exists("DocType", "SR Followup Status") else 0,
        "active_status_count": frappe.db.count("SR Followup Status", {"is_active": 1}) if frappe.db.exists("DocType", "SR Followup Status") else 0,
        "client_script": bool(frappe.db.exists("Client Script", "Patient Followup Status Active Filter"))
        if frappe.db.exists("DocType", "Client Script")
        else False,
    }


def remote_patient_followup_status_options(
    remote_doctype_file: str = DEFAULT_REMOTE_PATIENT_DOCTYPE_FILE,
) -> dict[str, object]:
    with open(remote_doctype_file, encoding="utf-8-sig") as handle:
        remote_data = json.load(handle)
    doctype_data = remote_data.get("data") or remote_data
    fields = doctype_data.get("fields") or []
    candidates = [
        field
        for field in fields
        if (field.get("label") or "").strip().lower() == "followup status"
        or field.get("fieldname") in {"sr_followup_status", "followup_status", "follow_up_status"}
    ]
    if not candidates:
        raise ValueError("Remote Patient DocType does not contain Followup Status field metadata.")
    field = candidates[0]
    options = (field.get("options") or "").replace("\r\n", "\n").strip()
    if not options:
        raise ValueError("Remote Followup Status field has no options.")
    return {
        "fieldname": field.get("fieldname"),
        "label": field.get("label"),
        "fieldtype": field.get("fieldtype"),
        "options": options,
        "option_count": len([line for line in options.split("\n") if line.strip()]),
    }


def sync_patient_followup_status_options_from_file(
    remote_doctype_file: str = DEFAULT_REMOTE_PATIENT_DOCTYPE_FILE,
) -> dict[str, object]:
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    remote_info = remote_patient_followup_status_options(remote_doctype_file)
    local_fieldname = patient_followup_status_field()
    local_info = patient_followup_status_info()
    options = remote_info["options"]
    custom_field_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Patient", "fieldname": local_fieldname},
        "name",
    )
    if custom_field_name:
        frappe.db.set_value("Custom Field", custom_field_name, "fieldtype", remote_info.get("fieldtype") or "Select")
        frappe.db.set_value("Custom Field", custom_field_name, "options", options)
    else:
        make_property_setter("Patient", local_fieldname, "fieldtype", remote_info.get("fieldtype") or "Select", "Data", validate_fields_for_doctype=False)
        make_property_setter("Patient", local_fieldname, "options", options, "Text", validate_fields_for_doctype=False)
    frappe.clear_cache(doctype="Patient")
    frappe.db.commit()
    return {
        "local_fieldname": local_fieldname,
        "remote_fieldname": remote_info.get("fieldname"),
        "old_option_count": len([line for line in (local_info.get("options") or "").split("\n") if line.strip()]),
        "new_option_count": remote_info["option_count"],
        "options": options,
    }


def sync_patient_followup_status_list_from_file(
    remote_status_file: str = DEFAULT_REMOTE_SR_FOLLOWUP_STATUS_FILE,
) -> dict[str, object]:
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    with open(remote_status_file, encoding="utf-8-sig") as handle:
        remote_data = json.load(handle)
    rows = remote_data.get("data") or remote_data
    names = sorted({(row.get("name") or "").strip() for row in rows if (row.get("name") or "").strip()})
    if not names:
        raise ValueError("Remote SR Followup Status file has no status rows.")

    fieldname = patient_followup_status_field()
    local_info = patient_followup_status_info()
    options = "\n".join(names)
    custom_field_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Patient", "fieldname": fieldname},
        "name",
    )
    if custom_field_name:
        frappe.db.set_value("Custom Field", custom_field_name, "fieldtype", "Select")
        frappe.db.set_value("Custom Field", custom_field_name, "options", options)
    make_property_setter("Patient", fieldname, "fieldtype", "Select", "Data", validate_fields_for_doctype=False)
    make_property_setter("Patient", fieldname, "options", options, "Text", validate_fields_for_doctype=False)
    frappe.clear_cache(doctype="Patient")
    frappe.db.commit()
    return {
        "fieldname": fieldname,
        "old_fieldtype": local_info.get("fieldtype"),
        "old_option_count": len([line for line in (local_info.get("options") or "").split("\n") if line.strip()]),
        "new_option_count": len(names),
        "options": options,
    }


def sync_patient_followup_status_link_from_file(
    remote_status_file: str = DEFAULT_REMOTE_SR_FOLLOWUP_STATUS_FILE,
    remote_doctype_file: str = DEFAULT_REMOTE_SR_FOLLOWUP_STATUS_DOCTYPE_FILE,
) -> dict[str, object]:
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    ensure_sr_followup_status_doctype(remote_doctype_file)
    inserted, updated = upsert_sr_followup_status_rows(remote_status_file)
    fieldname = patient_followup_status_field()
    custom_field_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Patient", "fieldname": fieldname},
        "name",
    )
    if custom_field_name:
        frappe.db.set_value("Custom Field", custom_field_name, "fieldtype", "Link")
        frappe.db.set_value("Custom Field", custom_field_name, "options", "SR Followup Status")
    make_property_setter("Patient", fieldname, "fieldtype", "Link", "Data", validate_fields_for_doctype=False)
    make_property_setter("Patient", fieldname, "options", "SR Followup Status", "Text", validate_fields_for_doctype=False)
    ensure_patient_followup_status_filter_script()
    frappe.clear_cache(doctype="Patient")
    frappe.clear_cache(doctype="SR Followup Status")
    frappe.db.commit()
    return {
        "fieldname": fieldname,
        "fieldtype": "Link",
        "options": "SR Followup Status",
        "inserted_statuses": inserted,
        "updated_statuses": updated,
        "active_statuses": frappe.db.count("SR Followup Status", {"is_active": 1}),
    }


def ensure_patient_followup_status_filter_script() -> bool:
    if not frappe.db.exists("DocType", "Client Script"):
        return False
    script_name = "Patient Followup Status Active Filter"
    script = """
frappe.ui.form.on('Patient', {
\tsetup(frm) {
\t\tfrm.set_query('sr_followup_status', () => ({
\t\t\tfilters: {
\t\t\t\tis_active: 1
\t\t\t}
\t\t}));
\t}
});
""".strip()
    if frappe.db.exists("Client Script", script_name):
        doc = frappe.get_doc("Client Script", script_name)
        doc.dt = "Patient"
        doc.view = "Form"
        doc.enabled = 1
        doc.script = script
        doc.save(ignore_permissions=True)
        return True
    doc = frappe.get_doc(
        {
            "doctype": "Client Script",
            "name": script_name,
            "dt": "Patient",
            "view": "Form",
            "enabled": 1,
            "script": script,
        }
    )
    doc.insert(ignore_permissions=True)
    return True


def ensure_sr_followup_status_doctype(
    remote_doctype_file: str = DEFAULT_REMOTE_SR_FOLLOWUP_STATUS_DOCTYPE_FILE,
) -> None:
    if frappe.db.exists("DocType", "SR Followup Status"):
        return
    with open(remote_doctype_file, encoding="utf-8-sig") as handle:
        remote_data = json.load(handle)
    remote = remote_data.get("data") or remote_data
    module = remote.get("module") or "Custom"
    if not frappe.db.exists("Module Def", module):
        module = "Custom" if frappe.db.exists("Module Def", "Custom") else "Core"
    doc = frappe.get_doc(
        {
            "doctype": "DocType",
            "name": "SR Followup Status",
            "module": module,
            "custom": 1,
            "autoname": "field:status_name",
            "title_field": "status_name",
            "search_fields": "status_name",
            "sort_field": "modified",
            "sort_order": "DESC",
            "fields": [
                {"fieldname": "naming_series", "label": "Series", "fieldtype": "Select", "options": "FUP-.#####", "reqd": 1},
                {"fieldname": "status_name", "label": "Status Name", "fieldtype": "Data", "reqd": 1, "in_list_view": 1},
                {"fieldname": "description", "label": "Description", "fieldtype": "Small Text"},
                {"fieldname": "is_active", "label": "Is Active", "fieldtype": "Check", "in_list_view": 1},
                {"fieldname": "color", "label": "Color", "fieldtype": "Color"},
                {"fieldname": "sort_order", "label": "Sort Order", "fieldtype": "Int"},
            ],
            "permissions": [
                {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
                {"role": "All", "read": 1},
            ],
        }
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(doctype="SR Followup Status")


def upsert_sr_followup_status_rows(
    remote_status_file: str = DEFAULT_REMOTE_SR_FOLLOWUP_STATUS_FILE,
) -> tuple[int, int]:
    with open(remote_status_file, encoding="utf-8-sig") as handle:
        remote_data = json.load(handle)
    rows = remote_data.get("data") or remote_data
    inserted = 0
    updated = 0
    for row in rows:
        name = (row.get("name") or row.get("status_name") or "").strip()
        if not name:
            continue
        values = {
            "doctype": "SR Followup Status",
            "name": name,
            "naming_series": row.get("naming_series") or "FUP-.#####",
            "status_name": row.get("status_name") or name,
            "description": row.get("description") or "",
            "is_active": frappe.utils.cint(row.get("is_active")),
            "color": row.get("color") or "",
            "sort_order": frappe.utils.cint(row.get("sort_order")),
        }
        if frappe.db.exists("SR Followup Status", name):
            doc = frappe.get_doc("SR Followup Status", name)
            doc.update(values)
            doc.db_update()
            updated += 1
        else:
            doc = frappe.get_doc(values)
            doc.db_insert()
            inserted += 1
    frappe.db.commit()
    return inserted, updated


def undo_last_patient_followup_sync() -> dict[str, object]:
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    patient = "HLC-PAT-2026-00001"
    fieldname = patient_followup_status_field()
    restored_options = "Pending\nDone\nAgent Not Available"
    custom_field_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Patient", "fieldname": fieldname},
        "name",
    )
    if custom_field_name:
        frappe.db.set_value("Custom Field", custom_field_name, "fieldtype", "Select")
        frappe.db.set_value("Custom Field", custom_field_name, "options", restored_options)
    make_property_setter("Patient", fieldname, "fieldtype", "Select", "Data", validate_fields_for_doctype=False)
    make_property_setter("Patient", fieldname, "options", restored_options, "Text", validate_fields_for_doctype=False)
    old_value = frappe.db.get_value("Patient", patient, fieldname) if frappe.db.exists("Patient", patient) else None
    if old_value is not None:
        frappe.db.set_value("Patient", patient, fieldname, "", update_modified=False)
    frappe.clear_cache(doctype="Patient")
    frappe.db.commit()
    return {
        "patient": patient,
        "fieldname": fieldname,
        "old_value": old_value,
        "new_value": "",
        "restored_options": restored_options,
    }


def parse_doctypes(value) -> list[str]:
    if not value:
        return list(ANALYTICS_DOCTYPES)
    if isinstance(value, str):
        try:
            parsed = frappe.parse_json(value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item or "").strip()]
        except Exception:
            return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return list(ANALYTICS_DOCTYPES)


def read_token(path: str) -> tuple[str, str]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    api_key = (row.get("api_key") or "").strip()
    api_secret = (row.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        raise ValueError("CSV must contain api_key and api_secret columns.")
    return api_key, api_secret


def remote_get(session: requests.Session, base_url: str, path: str, params: dict | None = None) -> dict:
    response = session.get(f"{base_url.rstrip('/')}{path}", params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def remote_doc_names(session: requests.Session, base_url: str, doctype: str, page_length: int) -> list[str]:
    names: list[str] = []
    for page in iter_remote_doc_name_pages(session, base_url, doctype, page_length):
        names.extend(page)
    return names


def iter_remote_doc_name_pages(session: requests.Session, base_url: str, doctype: str, page_length: int):
    start = 0
    while True:
        payload = remote_get(
            session,
            base_url,
            f"/api/resource/{quote(doctype)}",
            {
                "fields": json.dumps(["name"]),
                "limit_start": start,
                "limit_page_length": page_length,
                "order_by": "modified asc",
            },
        )
        rows = payload.get("data") or []
        if not rows:
            break
        yield [row["name"] for row in rows if row.get("name")]
        if len(rows) < page_length:
            break
        start += page_length


def remote_doc(session: requests.Session, base_url: str, doctype: str, name: str) -> dict:
    payload = remote_get(session, base_url, f"/api/resource/{quote(doctype)}/{quote(name, safe='')}")
    return payload.get("data") or {}


def doc_payload_for_local(remote_doc_data: dict, doctype: str) -> dict:
    meta = frappe.get_meta(doctype)
    valid_fields = {"name"} | {field.fieldname for field in meta.fields}
    child_table_fields = {field.fieldname for field in meta.fields if field.fieldtype == "Table"}
    payload: dict = {"doctype": doctype, "name": remote_doc_data.get("name")}
    for fieldname, value in remote_doc_data.items():
        if fieldname in SYSTEM_FIELDS or fieldname not in valid_fields:
            continue
        if fieldname in child_table_fields and isinstance(value, list):
            payload[fieldname] = [
                {
                    key: child_value
                    for key, child_value in child.items()
                    if key not in SYSTEM_FIELDS and key not in {"name", "parent", "parentfield", "parenttype"}
                }
                for child in value
            ]
        else:
            payload[fieldname] = value
    return payload


def import_doctype(session: requests.Session, base_url: str, doctype: str, page_length: int, dry_run: bool) -> dict:
    if not frappe.db.exists("DocType", doctype):
        return {"doctype": doctype, "skipped": 1, "reason": "missing_local_doctype"}
    result = {"doctype": doctype, "remote": 0, "inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    processed = 0
    for names in iter_remote_doc_name_pages(session, base_url, doctype, min(max(int(page_length), 1), 500)):
        # Finish all network I/O for the page before opening its database write transaction.
        remote_rows = []
        for name in names:
            try:
                remote_rows.append(remote_doc(session, base_url, doctype, name))
            except Exception as exc:
                result["errors"] += 1
                print(f"{doctype}: failed to fetch {name}: {exc}", file=sys.stderr)
        result["remote"] += len(names)
        payloads = [doc_payload_for_local(row, doctype) for row in remote_rows if row.get("name")]
        if dry_run:
            existing = set(
                frappe.get_all(
                    doctype,
                    filters={"name": ["in", [row["name"] for row in payloads]]},
                    pluck="name",
                    limit_start=0,
                    limit_page_length=max(1, len(payloads)),
                )
            )
            result["updated"] += sum(1 for row in payloads if row["name"] in existing)
            result["inserted"] += sum(1 for row in payloads if row["name"] not in existing)
            processed += len(payloads)
            continue
        try:
            inserted, updated = _bulk_upsert_parent_rows(doctype, payloads)
            result["inserted"] += inserted
            result["updated"] += updated
            processed += len(payloads)
            frappe.db.commit()
            print(f"{doctype}: {processed}/{result['remote']}")
        except Exception as exc:
            result["errors"] += len(payloads)
            print(f"{doctype}: failed page ending at {names[-1] if names else '-'}: {exc}", file=sys.stderr)
            frappe.db.rollback()
    return result


def _bulk_upsert_parent_rows(doctype: str, payloads: list[dict]) -> tuple[int, int]:
    if not payloads:
        return 0, 0
    names = [row["name"] for row in payloads]
    existing = set(
        frappe.get_all(
            doctype,
            filters={"name": ["in", names]},
            pluck="name",
            limit_start=0,
            limit_page_length=len(names),
        )
    )
    updates = {
        row["name"]: _scalar_parent_values(row)
        for row in payloads
        if row["name"] in existing
    }
    if updates:
        frappe.db.bulk_update(doctype, updates, chunk_size=500)

    inserts = [row for row in payloads if row["name"] not in existing]
    if inserts:
        value_fields = sorted({field for row in inserts for field in _scalar_parent_values(row)})
        fields = ["name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", *value_fields]
        now = frappe.utils.now()
        values = [
            [
                row["name"],
                frappe.session.user or "Administrator",
                now,
                now,
                frappe.session.user or "Administrator",
                0,
                0,
                *[_scalar_parent_values(row).get(field) for field in value_fields],
            ]
            for row in inserts
        ]
        frappe.db.bulk_insert(doctype, fields, values, ignore_duplicates=True, chunk_size=500)
    return len(inserts), len(updates)


def _scalar_parent_values(payload: dict) -> dict:
    return {
        fieldname: value
        for fieldname, value in payload.items()
        if fieldname not in {"doctype", "name"} and not isinstance(value, (list, dict))
    }
