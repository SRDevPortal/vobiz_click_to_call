"""Count an external provider callback once, including nested routing handlers."""
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import re

import frappe

_active = ContextVar("vobiz_callback_limit_active", default=False)
WINDOW_SECONDS = 60

# Use the existing raw Redis key and count rejected attempts, just as Frappe's
# method limiter does. INCR and expiry inspection run without another request
# or key expiration interleaving between them. A near-expiry TTL of 0 is valid.
_COUNT_CALLBACK = """
local existed = redis.call('EXISTS', KEYS[1])
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('PTTL', KEYS[1])
local repaired = 0
if ttl == -1 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
    ttl = redis.call('PTTL', KEYS[1])
    repaired = existed
end
return {count, ttl, repaired}
"""

_REPAIR_CALLBACK = """
if redis.call('PTTL', KEYS[1]) == -1 then
    return redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return 0
"""

# Exact callback commands only: never include browser APIs, sessions, provider
# read budgets or the separate Frappe site-wide limiter in the repair scan.
CALLBACK_COMMANDS = (
    "vobiz_system_call.api.webrtc.answer",
    "vobiz_system_call.api.webrtc.fallback",
    "vobiz_system_call.api.webrtc.hangup",
    "vobiz_system_call.api.webrtc.provider_event",
    "vobiz_system_call.api.webrtc.incoming_action",
    "vobiz_click_to_call.api.inbound.route",
    "vobiz_click_to_call.api.inbound.dial_action",
    "vobiz_click_to_call.api.inbound.dial_event",
    "vobiz_click_to_call.api.webhook.answer",
    "vobiz_click_to_call.api.webhook.ring",
    "vobiz_click_to_call.api.webhook.dial_callback",
    "vobiz_click_to_call.api.webhook.dial_action",
    "vobiz_click_to_call.api.webhook.hangup",
    "vobiz_click_to_call.api.webhook.fallback",
    "vobiz_click_to_call.api.webhook.recording_callback",
    "vobiz_click_to_call.api.webhook.transcription_callback",
    "vobiz_click_to_call.api.webhook.transcription_event",
)


def _count_callback(cache, key):
    # Do not fall back to non-atomic commands or bypass limits on Redis errors.
    return tuple(int(value) for value in cache.eval(
        _COUNT_CALLBACK, 1, key, WINDOW_SECONDS * 1000,
    ))


def repair_callback_counter_expiry():
    """Idempotently attach expiry to this site's existing callback counters.

    Run in a selected Frappe site context. Preserve the count and any existing
    expiry; an over-limit counter stays blocked until its repaired window ends.
    """
    cache = frappe.cache
    prefix = cache.make_key("rl:vobiz_")
    if isinstance(prefix, str):
        prefix = prefix.encode()
    # Escape Redis glob characters in the site namespace before using SCAN.
    pattern = b"".join(b"\\" + bytes([c]) if c in b"*?[]\\" else bytes([c]) for c in prefix) + b"*"
    allowed = tuple(cache.make_key(f"rl:{cmd}:") for cmd in CALLBACK_COMMANDS)
    allowed = tuple(p.encode() if isinstance(p, str) else p for p in allowed)
    examined = repaired = 0
    for key in cache.scan_iter(match=pattern, count=200):
        raw_key = key.encode() if isinstance(key, str) else key
        if not raw_key.startswith(allowed):
            continue
        examined += 1
        repaired += int(cache.eval(_REPAIR_CALLBACK, 1, raw_key, WINDOW_SECONDS * 1000))
    return {"examined": examined, "repaired": repaired, "window_seconds": WINDOW_SECONDS}


def _log_counter(event, fn, endpoint, limit, count, ttl_ms):
    # Deliberately omit request URLs, tokens, headers and callback payloads.
    try:
        entry = {
            "event": event, "handler": fn.__module__ + "." + fn.__name__,
            "endpoint": frappe.form_dict.get("cmd"), "source_ip": frappe.local.request_ip,
            "category": endpoint, "limit": limit, "counter": count,
            "ttl_ms": ttl_ms, "window_seconds": WINDOW_SECONDS,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        for field in ("call_log", "CallUUID"):
            value = frappe.form_dict.get(field)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                entry[field] = value
        frappe.logger("vobiz_callbacks").warning(entry)
    except Exception:
        # Diagnostics must not alter whether a callback is allowed or rejected.
        pass


def callback_request_limit(endpoint, default=300):
    value = frappe.conf.get("vobiz_" + endpoint + "_requests_per_minute", default)
    try:
        if isinstance(value, bool) or str(value).strip() != str(int(value)):
            return default
        value = int(value)
    except (ValueError, TypeError, OverflowError):
        return default
    return value if 1 <= value <= 60000 else default


def callback_rate_limit(endpoint, default=300):
    """Retain Frappe's endpoint/IP limit; internal calls are not new HTTP requests.

    Enter the scope only after the outer limiter accepts the request. Always
    reset it, including errors, so later requests cannot inherit an exemption.
    Authentication remains in each handler, including nested handlers.
    """
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if _active.get() or not frappe.request:
                return fn(*args, **kwargs)
            ip = frappe.local.request_ip
            if not ip:
                frappe.throw(frappe._("Either key or IP flag is required."))
            cache = frappe.cache
            key = cache.make_key(f"rl:{frappe.form_dict.cmd}:{ip}")
            limit = callback_request_limit(endpoint, default)
            count, ttl_ms, repaired = _count_callback(cache, key)
            if repaired:
                _log_counter("callback_counter_expiry_repaired", fn, endpoint, limit, count, ttl_ms)
            if count > limit:
                _log_counter("callback_rate_limited", fn, endpoint, limit, count, ttl_ms)
                frappe.throw(
                    frappe._("You hit the rate limit because of too many requests. Please try after sometime."),
                    frappe.RateLimitExceededError,
                )
            token = _active.set(True)
            try:
                return fn(*args, **kwargs)
            finally:
                _active.reset(token)
        return wrapper
    return decorate
