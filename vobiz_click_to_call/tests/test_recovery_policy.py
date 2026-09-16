"""Recovery tests run without a site, provider connection, or real calls."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
import requests
from vobiz_click_to_call.services import client, recovery_policy as policy


class MemoryCache:
    def __init__(self):
        self.values, self.locks = {}, set()

    def get_value(self, key, **kwargs):
        return copy.deepcopy(self.values.get(key))

    def set_value(self, key, value, **kwargs):
        self.values[key] = copy.deepcopy(value)

    def delete_value(self, key):
        self.values.pop(key, None)

    def lock(self, key, **kwargs):
        def acquire(**kwargs):
            if key in self.locks:
                return False
            self.locks.add(key)
            return True
        return SimpleNamespace(acquire=acquire, release=lambda: self.locks.remove(key))

    def make_key(self, key):
        return key

    def pipeline(self, **kwargs):
        pipe = MagicMock()
        def execute():
            key = pipe.incr.call_args.args[0]
            self.values[key] = self.values.get(key, 0) + 1
            return [self.values[key], True]
        pipe.execute.side_effect = execute
        return pipe


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.cache = MemoryCache()
        self.clock = 1000.0
        self.errors = MagicMock()
        for obj, name, value in [
            (frappe, "cache", lambda: self.cache), (frappe, "conf", frappe._dict()),
            (frappe, "log_error", self.errors), (frappe, "as_json", json.dumps),
            (policy.time, "time", lambda: self.clock), (policy.random, "uniform", lambda *a: 0),
        ]:
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_timeout_retains_state_and_waits_for_retry_after_without_sleep(self):
        with policy.attempt("CALL") as allowed:
            self.assertTrue(allowed)
            raise client.ProviderTemporaryError("temporary", retry_after=120)
        self.assertFalse(policy.due("CALL"))
        self.clock += 119
        with policy.attempt("CALL") as allowed:
            self.assertFalse(allowed)
        self.clock += 1
        self.assertTrue(policy.due("CALL"))
        self.errors.assert_not_called()

    def test_workers_cannot_overlap_but_other_calls_are_independent(self):
        with policy.attempt("CALL") as allowed:
            self.assertTrue(allowed)
            with policy.attempt("CALL") as second:
                self.assertFalse(second)
            with policy.attempt("OTHER") as other:
                self.assertTrue(other)
        self.assertFalse(self.cache.locks)

    def test_delay_is_bounded_and_only_one_alert_is_written(self):
        for _ in range(12):
            with policy.attempt("CALL") as allowed:
                self.assertTrue(allowed)
                raise client.ProviderTemporaryError("provider slow")
            state = self.cache.get_value(policy.state_key("CALL"))
            self.assertLessEqual(state["next_attempt"] - self.clock, 900)
            self.clock = state["next_attempt"]
        self.errors.assert_called_once()

    def test_successful_completion_does_not_resurrect_cooldown_or_alert(self):
        self.cache.set_value(policy.state_key("CALL"), {"attempts": 7})
        with policy.attempt("CALL"):
            policy.clear("CALL")
        self.assertIsNone(self.cache.get_value(policy.state_key("CALL")))
        self.errors.assert_not_called()

    def test_unexpected_error_is_not_silenced_and_lock_is_released(self):
        with self.assertRaisesRegex(ValueError, "bad data"):
            with policy.attempt("CALL"):
                raise ValueError("bad data")
        self.assertFalse(self.cache.locks)

    def test_account_read_budget_is_shared_and_resets_next_minute(self):
        frappe.conf.vobiz_provider_reads_per_minute = 2
        policy.claim_provider_read("account")
        policy.claim_provider_read("account")
        with self.assertRaises(client.ProviderTemporaryError):
            policy.claim_provider_read("account")
        policy.claim_provider_read("another-account")
        self.clock += 60
        policy.claim_provider_read("account")

    def provider(self):
        obj = object.__new__(client.VobizClient)
        obj.auth_id, obj.auth_token = "test-id", "test-token"
        obj.base_url, obj.timeout = "https://provider.invalid", 20
        return obj

    def test_read_uses_configured_timeout_and_no_inline_retries(self):
        with patch.object(client.requests, "get", side_effect=requests.ReadTimeout("sensitive request")) as get:
            with self.assertRaises(client.ProviderTemporaryError) as result:
                self.provider().search_cdrs()
        self.assertNotIn("sensitive", str(result.exception))
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 20))

    def test_rate_limit_and_outages_are_retryable_but_404_is_only_missing_cdr(self):
        provider = self.provider()
        for code in (408, 429, 500, 502, 503, 504):
            with self.subTest(code=code), patch.object(client.requests, "get", return_value=MagicMock(
                    status_code=code, headers={"Retry-After": "120"})):
                with self.assertRaises(client.ProviderTemporaryError) as result:
                    provider.search_cdrs()
                self.assertEqual(result.exception.retry_after, 120)
        with patch.object(client.requests, "get", return_value=MagicMock(status_code=404)):
            self.assertEqual(provider.retrieve_cdr("CALL"), {})

    def test_call_creation_timeout_is_never_automatically_retried(self):
        with patch.object(client.requests, "post", side_effect=requests.ReadTimeout()) as post:
            with self.assertRaises(requests.ReadTimeout):
                self.provider().make_call({"to": "test"})
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
