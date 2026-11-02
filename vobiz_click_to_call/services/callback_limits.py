"""Count an external provider callback once, including nested routing handlers."""
from contextvars import ContextVar
from functools import wraps

import frappe
from frappe.rate_limiter import rate_limit

_active = ContextVar("vobiz_callback_limit_active", default=False)


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
        def scoped(*args, **kwargs):
            token = _active.set(True)
            try:
                return fn(*args, **kwargs)
            finally:
                _active.reset(token)

        limited = rate_limit(limit=lambda: callback_request_limit(endpoint, default), seconds=60)(scoped)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if _active.get():
                return fn(*args, **kwargs)
            try:
                return limited(*args, **kwargs)
            except frappe.RateLimitExceededError:
                try:
                    frappe.logger("vobiz_callbacks").warning({
                        "event": "callback_rate_limited", "handler": fn.__module__ + "." + fn.__name__,
                        "category": endpoint, "limit": callback_request_limit(endpoint, default),
                        "window_seconds": 60,
                    })
                except Exception:
                    pass
                raise
        return wrapper
    return decorate
