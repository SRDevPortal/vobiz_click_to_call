from __future__ import annotations

from typing import Any

import frappe


def snapshot_doc(doc) -> dict[str, Any]:
    return {
        field.fieldname: doc.get(field.fieldname)
        for field in doc.meta.fields
        if field.fieldtype not in {"Table", "Table MultiSelect"}
    }


def save_doc_latest(doc, before: dict[str, Any] | None = None, *, ignore_permissions: bool = True):
    try:
        doc.save(ignore_permissions=ignore_permissions)
        return doc
    except frappe.TimestampMismatchError:
        if not before or not doc.name:
            raise

        changed_values = {
            fieldname: doc.get(fieldname)
            for fieldname, old_value in before.items()
            if doc.get(fieldname) != old_value
        }
        if not changed_values:
            return frappe.get_doc(doc.doctype, doc.name)

        for _attempt in range(3):
            latest = frappe.get_doc(doc.doctype, doc.name)
            for fieldname, value in changed_values.items():
                latest.set(fieldname, value)
            try:
                latest.save(ignore_permissions=ignore_permissions)
                return latest
            except frappe.TimestampMismatchError:
                continue
        raise
