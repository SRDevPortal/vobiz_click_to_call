"""Mapping recovery in isolated transactions, always mapping before call log."""
from __future__ import annotations

import hashlib
import json

import frappe

TERMINAL_STATUSES = frozenset({
    "Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled",
})
RECOVERY_BATCH_SIZE = 100


def enqueue_recovery(call_log: str, mapping_name: str | None = None) -> None:
    if not call_log:
        return
    frappe.enqueue(
        "vobiz_click_to_call.services.mapping_recovery.recover_mapping",
        call_log=call_log, mapping_name=mapping_name, queue="short", timeout=120,
        enqueue_after_commit=True,
        job_id="vobiz-restore-" + hashlib.sha256((call_log + "\0" + (mapping_name or "")).encode()).hexdigest(),
        deduplicate=True,
    )


def recover_mapping(call_log: str, mapping_name: str | None = None) -> None:
    """Background-job boundary: database conflicts retry this whole transaction."""
    try:
        if mapping_name:
            _recover_locked(mapping_name, call_log)
        else:
            names = frappe.get_all(
                "Vobiz User Mapping", filters={"current_call_log": call_log},
                pluck="name", order_by="name asc",
            )
            for name in names:
                enqueue_recovery(call_log, name)
    except (frappe.QueryDeadlockError, frappe.QueryTimeoutError) as exc:
        raise frappe.RetryBackgroundJobError from exc


def recover_final_mapping(mapping_name: str, call_log: str) -> bool:
    """Release a known final call during an availability read, without provider I/O."""
    if not call_log or frappe.db.get_value("Vobiz Call Log", call_log, "status") not in TERMINAL_STATUSES:
        return False
    changed = bool(_recover_locked(mapping_name, call_log, terminal_only=True))
    frappe.db.commit()
    return changed


def _recover_locked(mapping_name: str, call_log: str, *, terminal_only=False) -> bool:
    mappings = frappe.db.sql(
        """SELECT name, user, enabled, availability_status, auto_available_after_call,
                  current_call_log
           FROM `tabVobiz User Mapping` WHERE name=%s FOR UPDATE""",
        (mapping_name,), as_dict=True,
    )
    if not mappings or mappings[0].current_call_log != call_log:
        return  # A delayed callback must never release a newer call.
    mapping = mappings[0]
    calls = frappe.db.sql(
        """SELECT name, status, request_json, modified FROM `tabVobiz Call Log`
           WHERE name=%s FOR UPDATE""", (call_log,), as_dict=True,
    )
    call = calls[0] if calls else None
    if terminal_only and (not call or call.status not in TERMINAL_STATUSES):
        return False
    try:
        context = json.loads((call.request_json if call else None) or "{}")
    except (TypeError, ValueError):
        context = {}
    if not isinstance(context, dict):
        context = {}
    managed = context.get("source") == "vobiz_system_call"
    if call and call.status not in TERMINAL_STATUSES:
        if call.modified and frappe.utils.time_diff_in_seconds(frappe.utils.now(), call.modified) < 60:
            return
        if managed:
            from vobiz_system_call.api.lifecycle import enqueue_reconcile
            enqueue_reconcile(call_log)
        else:
            # A clock alone cannot establish that a provider call has ended.
            frappe.enqueue(
                "vobiz_click_to_call.services.mapping_recovery.reconcile_legacy_call",
                call_log=call_log, queue="short", timeout=120, enqueue_after_commit=True,
                job_id="vobiz-recover-cdr-" + call_log, deduplicate=True,
            )
        return
    browser = managed and context.get("call_device") == "Browser Softphone"
    present = True
    if browser:
        from vobiz_system_call.api.lifecycle import presence
        present = bool(presence(mapping.user))
    from vobiz_click_to_call.services.availability_recovery import after_call_state, record_transition
    status, reason = after_call_state(mapping, browser=browser, present=present)
    available = status == "Available"
    now = frappe.utils.now_datetime()
    frappe.db.sql(
        """UPDATE `tabVobiz User Mapping`
           SET current_call_log='', availability_status=%s, accept_calls=%s,
               last_status_at=%s, modified=%s, modified_by=%s
           WHERE name=%s AND current_call_log=%s""",
        (status, int(available), now, now, frappe.session.user, mapping_name, call_log),
    )
    record_transition(mapping, status, now, reason, call_log)
    frappe.publish_realtime(
        "vobiz_mapping_recovered", {"call_log": call_log}, user=mapping.user, after_commit=True,
    )

    return True


def reconcile_legacy_call(call_log: str) -> None:
    """Provider I/O runs in a separate job with no mapping/log locks held."""
    from requests.exceptions import Timeout, ConnectionError
    from vobiz_click_to_call.services.cdr import sync_call_log_cdr
    from vobiz_click_to_call.services import recovery_policy

    try:
        with recovery_policy.attempt(call_log) as allowed:
            if not allowed:
                return
            sync_call_log_cdr(call_log, ignore_permissions=True, strict_match=True)
            if frappe.db.get_value("Vobiz Call Log", call_log, "status") in TERMINAL_STATUSES:
                recovery_policy.clear(call_log)
                enqueue_recovery(call_log)
    except (frappe.QueryDeadlockError, frappe.QueryTimeoutError,
            frappe.TimestampMismatchError, Timeout, ConnectionError) as exc:
        raise frappe.RetryBackgroundJobError from exc


def enqueue_pending_recovery() -> dict:
    """Bounded keyset scan also repairs missed/failed enqueue attempts."""
    cache = frappe.cache()
    cursor = cache.get_value("vobiz:mapping-recovery-cursor") or ""
    mappings = frappe.get_all(
        "Vobiz User Mapping", filters={"name": [">", cursor], "current_call_log": ["is", "set"]},
        fields=["name", "current_call_log"], order_by="name asc", limit_page_length=RECOVERY_BATCH_SIZE,
    )
    calls = {}
    if mappings:
        calls = {row.name: row for row in frappe.get_all(
            "Vobiz Call Log", filters={"name": ["in", list({m.current_call_log for m in mappings})]},
            fields=["name", "status", "modified"], limit_page_length=len(mappings),
        )}
    queued = 0
    for mapping in mappings:
        call = calls.get(mapping.current_call_log)
        if call and call.status not in TERMINAL_STATUSES and call.modified:
            if frappe.utils.time_diff_in_seconds(frappe.utils.now(), call.modified) < 60:
                continue  # The worker would return immediately for this same state.
        enqueue_recovery(mapping.current_call_log, mapping.name)
        queued += 1
    cache.set_value("vobiz:mapping-recovery-cursor",
                    mappings[-1].name if len(mappings) == RECOVERY_BATCH_SIZE else "", expires_in_sec=3600)
    return {"checked": len(mappings), "queued": queued}
