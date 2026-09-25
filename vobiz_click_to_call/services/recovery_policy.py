"""Shared, cross-worker cooldowns for reconciliation and hangup operations.

Redis loss may reset a cooldown, but can never establish a call outcome. The
minute scheduler supplies retries; workers do not sleep or repeat call creation.
"""
from contextlib import contextmanager
import hashlib
import random
import time

import frappe
from redis.exceptions import LockNotOwnedError

from vobiz_click_to_call.services.client import ProviderTemporaryError

STATE_TTL = 7 * 24 * 3600


def state_key(call_log, purpose="reconcile"):
    return "vobiz:recovery:" + purpose + ":" + hashlib.sha256(call_log.encode()).hexdigest()


def due(call_log, purpose="reconcile"):
    state = frappe.cache().get_value(state_key(call_log, purpose), expires=True) or {}
    return float(state.get("next_attempt", 0)) <= time.time()


def retry_delay(call_log, purpose="reconcile"):
    state = frappe.cache().get_value(state_key(call_log, purpose), expires=True) or {}
    return max(1, int(float(state.get("next_attempt", 0)) - time.time()) + 1)


def clear(call_log, purpose="reconcile"):
    frappe.cache().delete_value(state_key(call_log, purpose))


@contextmanager
def attempt(call_log, purpose="reconcile"):
    """One worker per call; even successful-but-unresolved lookups back off."""
    cache = frappe.cache()
    key = state_key(call_log, purpose)
    lock = cache.lock(cache.make_key(key + ":lock"), timeout=300, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        yield False
        return
    try:
        state = cache.get_value(key, expires=True) or {}
        now = time.time()
        if float(state.get("next_attempt", 0)) > now:
            yield False
            return
        attempts = int(state.get("attempts", 0)) + 1
        # Keep checking unresolved calls at a low rate instead of abandoning them.
        delay = min(120 if purpose == "hangup" else 900, 30 * 2 ** min(attempts - 1, 5)) + random.uniform(0, 10)
        state.update(attempts=attempts, next_attempt=now + delay)
        state.setdefault("first_attempt", now)
        cache.set_value(key, state, expires_in_sec=STATE_TTL)
        try:
            yield True
        except ProviderTemporaryError as exc:
            if not lock.owned():
                return  # A replacement worker owns any newer retry state.
            # No call-status write and no traceback containing provider headers.
            state["last_error"] = str(exc)[:250]
            state["last_error_source"] = exc.source
            state["last_operation"] = exc.operation
            state["last_http_status"] = exc.status_code
            state["last_request_id"] = exc.request_id
            state["next_attempt"] = time.time() + max(delay, exc.retry_after) + random.uniform(0, 5)
            cache.set_value(key, state, expires_in_sec=STATE_TTL)
        if not lock.owned():
            return
        if (attempts >= 8 and not state.get("alerted")
                and cache.get_value(key, expires=True)):
            state["alerted"] = True
            cache.set_value(key, state, expires_in_sec=STATE_TTL)
            frappe.log_error(
                title="Vobiz reconciliation remains pending",
                message=frappe.as_json({"call_log": call_log, "purpose": purpose,
                                       "attempts": attempts, "last_error": state.get("last_error")}),
            )
    finally:
        try:
            lock.release()
        except LockNotOwnedError:
            # Redis atomically checked the token: an expired/replaced lease is
            # not ours to release. Do not turn completed reconciliation into a
            # failed transaction or mask the original exception.
            pass


def claim_provider_read(auth_id):
    """Shared account budget across recovery, CDR sync and UI verification."""
    cache = frappe.cache()
    now = time.time()
    account = hashlib.sha256(auth_id.encode()).hexdigest()[:24]
    key = cache.make_key(f"vobiz:provider-reads:{account}:{int(now // 60)}")
    # INCR and EXPIRE run atomically; a stopped worker cannot leave an immortal key.
    pipe = cache.pipeline(transaction=True)
    pipe.incr(key)
    pipe.expire(key, 120)
    count, _ = pipe.execute()
    try:
        limit = max(1, int(frappe.conf.get("vobiz_provider_reads_per_minute", 120)))
    except (ValueError, TypeError, OverflowError):
        limit = 120
    if count > limit:
        raise ProviderTemporaryError("Provider read budget exhausted", retry_after=60 - now % 60,
                                     operation="read", source="local_budget")
