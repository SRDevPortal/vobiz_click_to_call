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
            return frappe.get_doc(doc.doctype, doc.name, for_update=True)

        for _attempt in range(3):
            # A plain read may keep returning the old REPEATABLE READ snapshot.
            latest = frappe.get_doc(doc.doctype, doc.name, for_update=True)
            updates = dict(changed_values)
            terminal = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}
            if (doc.doctype == "Vobiz Call Log" and latest.get("status") in terminal
                    and "status" in updates and updates["status"] not in terminal):
                # A delayed ring/answer must not reopen a call already ended.
                for fieldname in ("status", "call_status", "dial_status", "hangup_cause", "end_time"):
                    updates.pop(fieldname, None)
            for fieldname, value in updates.items():
                latest.set(fieldname, value)
            try:
                latest.save(ignore_permissions=ignore_permissions)
                return latest
            except frappe.TimestampMismatchError:
                continue
        raise
