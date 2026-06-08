from __future__ import annotations

import frappe


def _is_system_manager(user: str) -> bool:
    return "System Manager" in frappe.get_roles(user)


def call_log_query(user: str) -> str | None:
    if _is_system_manager(user):
        return None
    return f"`tabVobiz Call Log`.`user` = {frappe.db.escape(user)}"


def call_log_has_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
    user = user or frappe.session.user
    if _is_system_manager(user):
        return True
    return permission_type in (None, "read", "print", "email", "export") and doc.user == user

