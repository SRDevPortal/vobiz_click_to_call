from __future__ import annotations

from typing import Any

import frappe

CONNECTED_STATUSES = {"Connected", "Completed"}
MISSED_STATUSES = {"Failed", "Busy", "No Answer", "Cancelled", "Canceled"}
TERMINAL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"}


def call_signal(row: dict[str, Any] | None, *extra_fields: str) -> str:
    row = row or {}
    fields = ("status", "call_status", "dial_status", "hangup_cause", "error_message", *extra_fields)
    return " ".join(str(row.get(fieldname) or "") for fieldname in fields).strip().lower().replace("_", "-")


def talk_seconds(row: dict[str, Any] | None) -> int:
    row = row or {}
    return frappe.utils.cint(row.get("billsec")) or frappe.utils.cint(row.get("duration")) or frappe.utils.cint(row.get("recording_duration"))


def has_talk_time(row: dict[str, Any] | None) -> bool:
    row = row or {}
    return (
        frappe.utils.cint(row.get("billsec")) > 0
        or frappe.utils.cint(row.get("recording_duration")) > 0
        or frappe.utils.cint(row.get("duration")) >= 30
    )


def status_from_provider(
    row: dict[str, Any] | None,
    *,
    previous: str = "",
    connected_on_talk_time: bool = True,
) -> str:
    row = row or {}
    signal = call_signal(row)
    previous = str(previous or "").strip()

    if connected_on_talk_time and has_talk_time(row):
        return "Completed"
    if "busy" in signal:
        return "Busy"
    if "no-answer" in signal or "no answer" in signal or "timeout" in signal or "unanswered" in signal:
        return "No Answer"
    if "cancel" in signal or "reject" in signal or "decline" in signal:
        return "Cancelled"
    if "fail" in signal or "error" in signal:
        return "Failed"
    if "answered" in signal or "connected" in signal or "in-progress" in signal or "in progress" in signal:
        return "Connected"
    if "completed" in signal or "hangup" in signal or "normal-clearing" in signal or "normal clearing" in signal:
        if previous in CONNECTED_STATUSES or previous == "Connected":
            return "Completed"
        if previous in {"Agent Answered", "Customer Answered", "Agent Ringing", "Queued", "Ringing"}:
            return "Cancelled"
        return previous or "Completed"
    return previous or ""


def status_bucket(row: dict[str, Any] | None) -> str:
    row = row or {}
    status = str(row.get("status") or "").strip()
    if has_talk_time(row):
        return "connected"
    if status in CONNECTED_STATUSES:
        return "connected"

    signal = call_signal(row)
    if "busy" in signal:
        return "busy"
    if "no-answer" in signal or "no answer" in signal or "timeout" in signal or "unanswered" in signal:
        return "no_answer"
    if "cancel" in signal or "reject" in signal or "decline" in signal:
        return "cancelled"
    if "fail" in signal or "error" in signal:
        return "failed"
    if status in MISSED_STATUSES:
        return "missed"
    return "other"


def normalize_status_values(current_status: str, values: dict[str, Any]) -> dict[str, Any]:
    normalized = status_from_provider(values, previous=current_status)
    if normalized:
        values["status"] = normalized
    return values
