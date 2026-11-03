"""Keep direct browser-call reporting tied to the authenticated customer leg."""
import json
from datetime import timezone
from zoneinfo import ZoneInfo

import frappe


def direct_customer_call(row):
    try:
        data = json.loads(row.get("request_json") or "{}")
    except (ValueError, TypeError):
        return False
    return (isinstance(data, dict) and data.get("source") == "vobiz_system_call"
            and data.get("call_device") == "Browser Softphone"
            and row.get("direction") == "Outgoing" and not data.get("conference_recovery"))


def matches_customer(row, cdr):
    uuid = str(row.get("b_leg_uuid") or "").strip()
    # A parent CDR may mention the child in another field. Only its own ID counts.
    return bool(uuid and uuid == str(cdr.get("uuid") or cdr.get("call_uuid") or "").strip())


def provider_datetime(value):
    if not value:
        return None
    try:
        dt = frappe.utils.get_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(frappe.utils.get_system_timezone())).replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError):
        return None
