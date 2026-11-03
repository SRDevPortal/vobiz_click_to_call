"""Availability policy and auditable, conditional recovery of lost browser presence.

Callers hold the mapping row lock. No provider I/O or schema change is required.
The cache marker is only a recovery hint: the committed status timestamp must
still match, so a later manual choice or logout always wins.
"""
from __future__ import annotations

from functools import partial
import logging

import frappe


def marker_key(user):
    return "vobiz:connection-offline:" + user


def after_call_state(mapping, *, browser=False, present=True):
    status = mapping.get("availability_status") or "Available"
    if status in ("Away", "Offline"):
        return status, "preserve_manual_status"
    if not frappe.utils.cint(mapping.get("enabled", 1)):
        return "Offline", "agent_disabled"
    if not frappe.utils.cint(mapping.get("auto_available_after_call")):
        return "Away", "auto_available_disabled"
    if browser and not present:
        return "Offline", "browser_presence_lost"
    return "Available", "call_completed"


def record_transition(mapping, status, at, reason, call_log=None):
    """Record only committed changes; diagnostics must not break a callback."""
    event = {
        "event": "availability_transition", "user": mapping.get("user"),
        "mapping": mapping.get("name"), "from": mapping.get("availability_status"),
        "to": status, "at": str(at), "reason": reason,
        "call_log": call_log or "", "actor": frappe.session.user,
    }
    frappe.db.after_commit.add(partial(_committed_transition, event))


def _committed_transition(event):
    try:
        cache = frappe.cache()
        key = marker_key(event["user"])
        if event["reason"] == "browser_presence_lost":
            cache.set_value(key, {"at": event["at"], "call_log": event["call_log"]},
                            expires_in_sec=86400)
        elif event["reason"] != "preserve_manual_status":
            cache.delete_value(key)
    except Exception:
        # Missing cache means no automatic recovery, never unsolicited availability.
        pass
    try:
        logger = frappe.logger("vobiz_availability", allow_site=True, max_size=2_000_000, file_count=10)
        logger.setLevel(logging.INFO)
        logger.info(frappe.as_json(event))
    except Exception:
        pass


def connection_marker(mapping):
    try:
        marker = frappe.cache().get_value(marker_key(mapping.get("user")), expires=True)
        if (isinstance(marker, dict) and mapping.get("availability_status") == "Offline"
                and mapping.get("last_status_at") and marker.get("at")
                and frappe.utils.get_datetime(mapping["last_status_at"])
                == frappe.utils.get_datetime(marker["at"])):
            return marker
    except Exception:
        pass
    return None


def restore_browser_availability(mapping):
    """Called only after an owning, registered browser heartbeat, under its lock."""
    marker = connection_marker(mapping)
    if (not marker or mapping.get("current_call_log")
            or not frappe.utils.cint(mapping.get("enabled"))
            or not frappe.utils.cint(mapping.get("browser_softphone_enabled"))
            or not frappe.utils.cint(mapping.get("auto_available_after_call"))):
        return False
    now = frappe.utils.now_datetime()
    updates = {"availability_status": "Available", "accept_calls": 1, "last_status_at": now}
    frappe.db.set_value("Vobiz User Mapping", mapping.name, updates)
    record_transition(mapping, "Available", now, "browser_reconnected", marker.get("call_log"))
    mapping.update(updates)
    frappe.publish_realtime("vobiz_mapping_recovered", {"reason": "browser_reconnected"},
                            user=mapping.user, after_commit=True)
    return True
