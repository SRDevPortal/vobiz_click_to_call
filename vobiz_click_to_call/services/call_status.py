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
    call_flow = str(row.get("call_flow") or "").strip()
    if call_flow == "Customer First":
        customer_answer_duration = _answered_duration_seconds(row)
        if customer_answer_duration > 0:
            return customer_answer_duration
    if call_flow == "Agent First" and frappe.utils.cint(row.get("duration")) > 0:
        return frappe.utils.cint(row.get("duration"))
    return 0


def billable_talk_seconds(row: dict[str, Any] | None) -> int:
    row = row or {}
    recording_duration = frappe.utils.cint(row.get("recording_duration"))
    if recording_duration > 3600:
        recording_duration = round(recording_duration / 1000)
    return recording_duration or frappe.utils.cint(row.get("billsec"))


def _answered_duration_seconds(row: dict[str, Any]) -> int:
    answer_time = row.get("answer_time")
    end_time = row.get("end_time")
    if not answer_time or not end_time:
        return 0
    try:
        answer_dt = frappe.utils.get_datetime(answer_time)
        end_dt = frappe.utils.get_datetime(end_time)
    except Exception:
        return 0
    return max(0, frappe.utils.cint((end_dt - answer_dt).total_seconds()))


def has_talk_time(row: dict[str, Any] | None) -> bool:
    row = row or {}
    return billable_talk_seconds(row) > 0


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
            return "No Answer"
        if previous in {"Agent Answered", "Customer Answered", "Agent Ringing", "Queued", "Ringing"}:
            return "Cancelled"
        return previous or "No Answer"
    return previous or ""


def status_bucket(row: dict[str, Any] | None) -> str:
    row = row or {}
    status = str(row.get("status") or "").strip()
    if has_talk_time(row):
        return "connected"

    signal = call_signal(row)
    if "busy" in signal:
        return "busy"
    if "no-answer" in signal or "no answer" in signal or "timeout" in signal or "unanswered" in signal:
        return "no_answer"
    if status in CONNECTED_STATUSES:
        return "no_answer"
    if "cancel" in signal or "reject" in signal or "decline" in signal:
        return "cancelled"
    if "fail" in signal or "error" in signal:
        return "failed"
    if status in MISSED_STATUSES:
        return "missed"
    return "other"


def is_inbound_missed_call(row: dict[str, Any] | None) -> bool:
    row = row or {}
    if str(row.get("direction") or "").strip() != "Incoming":
        return False
    return status_bucket(row) in {"missed", "busy", "no_answer", "failed", "cancelled"}


def normalize_status_values(current_status: str, values: dict[str, Any]) -> dict[str, Any]:
    normalized = status_from_provider(values, previous=current_status)
    if normalized:
        values["status"] = normalized
    return values
