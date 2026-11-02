import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from vobiz_click_to_call.services.callback_limits import callback_rate_limit


class CallbackLimitTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.counts = {}
        cache = MagicMock()
        cache.make_key.side_effect = lambda key: key
        cache.get.side_effect = lambda key: self.counts.get(key, 0)
        cache.setex.side_effect = lambda key, ttl, value: self.counts.__setitem__(key, value)
        def increment(key, amount):
            self.counts[key] = self.counts.get(key, 0) + amount
            return self.counts[key]
        cache.incrby.side_effect = increment
        def reject(message, exc):
            raise exc(message)
        for name, value in {
            "cache": cache, "request": SimpleNamespace(method="POST"),
            "local": SimpleNamespace(request_ip="192.0.2.1"),
            "form_dict": frappe._dict(cmd="test.answer"), "conf": frappe._dict(),
            "throw": reject, "_": lambda value: value, "logger": MagicMock(),
        }.items():
            self.stack.enter_context(patch.object(frappe, name, value))
        # The limiter imports its translation helper directly.
        self.stack.enter_context(patch("frappe.rate_limiter._", lambda value: value))

    def test_nested_handler_does_not_double_count_or_apply_lower_limit(self):
        @callback_rate_limit("event", default=1)
        def inner():
            return "valid XML"
        @callback_rate_limit("answer", default=2)
        def outer():
            return inner()
        self.assertEqual(outer(), "valid XML")
        self.assertEqual(outer(), "valid XML")
        with self.assertRaises(frappe.RateLimitExceededError):
            outer()
        self.assertEqual(list(self.counts.values()), [3])
        entry = frappe.logger.return_value.warning.call_args.args[0]
        self.assertEqual(entry["event"], "callback_rate_limited")
        self.assertEqual(entry["limit"], 2)
        self.assertNotIn("token", entry)

    def test_direct_handler_is_still_limited_and_endpoints_are_independent(self):
        @callback_rate_limit("event", default=1)
        def handler():
            return "OK"
        handler()
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()
        frappe.form_dict.cmd = "test.hangup"
        self.assertEqual(handler(), "OK")

    def test_exception_does_not_exempt_next_request(self):
        @callback_rate_limit("answer", default=1)
        def handler():
            raise ValueError("routing failed")
        with self.assertRaises(ValueError):
            handler()
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()

    def test_nested_authentication_is_not_skipped(self):
        @callback_rate_limit("event")
        def inner():
            raise PermissionError("invalid callback token")
        @callback_rate_limit("answer")
        def outer():
            return inner()
        with self.assertRaises(PermissionError):
            outer()

    def test_runtime_configuration_applies(self):
        @callback_rate_limit("answer", default=1)
        def handler():
            return "OK"
        frappe.conf.vobiz_answer_requests_per_minute = 2
        handler()
        handler()
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()
