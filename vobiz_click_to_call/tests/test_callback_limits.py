import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from vobiz_click_to_call.services.callback_limits import callback_rate_limit
from vobiz_click_to_call.services import callback_limits


class CallbackLimitTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.counts = {}
        cache = MagicMock()
        cache.make_key.side_effect = lambda key: key
        def increment(cache, key):
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key], 60000, 0
        self.stack.enter_context(patch.object(callback_limits, "_count_callback", side_effect=increment))
        def reject(message, exc=ValueError):
            raise exc(message)
        for name, value in {
            "cache": cache, "request": SimpleNamespace(method="POST"),
            "local": SimpleNamespace(request_ip="192.0.2.1"),
            "form_dict": frappe._dict(cmd="test.answer"), "conf": frappe._dict(),
            "throw": reject, "_": lambda value: value, "logger": MagicMock(),
        }.items():
            self.stack.enter_context(patch.object(frappe, name, value))

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
        self.assertEqual(entry["counter"], 3)
        self.assertEqual(entry["ttl_ms"], 60000)
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

    def test_no_http_request_does_not_consume_callback_allowance(self):
        with patch.object(frappe, "request", None):
            self.assertEqual(callback_rate_limit("answer")(lambda: "OK")(), "OK")
        self.assertEqual(self.counts, {})

    def test_missing_ip_is_not_a_shared_global_bucket(self):
        frappe.local.request_ip = None
        with self.assertRaises(ValueError):
            callback_rate_limit("answer")(lambda: "OK")()
        self.assertEqual(self.counts, {})

    def test_redis_failure_does_not_bypass_limiter(self):
        handler = MagicMock()
        with patch.object(callback_limits, "_count_callback", side_effect=ConnectionError("Redis down")):
            with self.assertRaises(ConnectionError):
                callback_rate_limit("answer")(handler)()
        handler.assert_not_called()
        self.assertFalse(callback_limits._active.get())

    def test_logs_omit_tokens_urls_headers_and_payload(self):
        frappe.form_dict.update(token="SECRET", url="https://example.invalid/?token=SECRET",
                                payload="SECRET", CallUUID="safe-uuid", call_log="CTC-test")
        handler = callback_rate_limit("answer", default=1)(lambda: "OK")
        handler()
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()
        entry = frappe.logger.return_value.warning.call_args.args[0]
        self.assertNotIn("SECRET", str(entry))
        self.assertEqual(entry["call_log"], "CTC-test")
        self.assertEqual(entry["CallUUID"], "safe-uuid")

    def test_diagnostic_failure_does_not_change_rejection(self):
        frappe.logger.side_effect = RuntimeError("log unavailable")
        with patch.object(callback_limits, "_count_callback", return_value=(9, 60000, 1)):
            with self.assertRaises(frappe.RateLimitExceededError):
                callback_rate_limit("answer", default=1)(lambda: "OK")()

    def test_configuration_validation_keeps_existing_fallback(self):
        for value in (True, 0, -1, 60001, "1.5", "bad", None):
            with self.subTest(value=value):
                frappe.conf.vobiz_answer_requests_per_minute = value
                self.assertEqual(callback_limits.callback_request_limit("answer", 600), 600)
