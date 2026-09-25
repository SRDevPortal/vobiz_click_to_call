import unittest
from unittest.mock import MagicMock, patch
from contextlib import ExitStack
import requests
import frappe
from vobiz_click_to_call.services import client, recovery_policy as policy, reference_sync
from vobiz_click_to_call.tests.test_recovery_policy import MemoryCache


class CoordinatedHangupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.cache = MemoryCache()
        self.now = 1000
        for obj, name, value in [
            (frappe, "cache", lambda: self.cache), (frappe, "conf", frappe._dict()),
            (frappe, "log_error", MagicMock()), (policy.time, "time", lambda: self.now),
            (policy.random, "uniform", lambda *a: 0),
        ]:
            self.stack.enter_context(patch.object(obj, name, value))
        self.provider = object.__new__(client.VobizClient)
        self.provider.auth_id, self.provider.auth_token = "account", "SECRET"
        self.provider.base_url, self.provider.timeout = "https://example.invalid", 20
        self.delete = self.stack.enter_context(patch.object(client.requests, "delete",
            return_value=MagicMock(status_code=204)))

    def test_browser_and_worker_share_one_request_but_other_uuid_remains_independent(self):
        self.provider.hangup_call("call")
        result = self.provider.hangup_call("call")
        self.assertTrue(result["request_deferred"])
        self.assertGreater(result["retry_after"], 0)
        self.delete.assert_called_once()
        self.provider.hangup_call("another")
        self.assertEqual(self.delete.call_count, 2)

    def test_provider_retry_after_blocks_all_callers_until_due(self):
        self.delete.return_value = MagicMock(status_code=429, headers={"Retry-After": "120", "X-Request-ID": "r1"})
        result = self.provider.hangup_call("call")
        self.assertTrue(result["pending_provider"])
        self.now += 119
        self.provider.hangup_call("call")
        self.delete.assert_called_once()
        self.now += 2
        self.delete.return_value = MagicMock(status_code=204)
        self.provider.hangup_call("call")
        self.assertEqual(self.delete.call_count, 2)

    def test_failed_delete_never_means_ended_and_is_not_retried_inline(self):
        self.delete.side_effect = requests.ReadTimeout("SECRET request headers")
        result = self.provider.hangup_call("call")
        self.assertTrue(result["pending_provider"])
        self.delete.assert_called_once()
        state = self.cache.get_value(policy.state_key("account:call", "hangup"))
        self.assertNotIn("SECRET", str(state))

    def test_read_cooldown_cannot_block_urgent_hangup(self):
        with policy.attempt("call"):
            pass
        self.provider.hangup_call("call")
        self.delete.assert_called_once()

    def test_temporary_error_preserves_metadata_without_response_body(self):
        response = MagicMock(status_code=429, headers={"Retry-After": "60", "X-Request-ID": "r1"})
        with self.assertRaises(client.ProviderTemporaryError) as result:
            self.provider._raise_temporary(response, "hangup")
        err = result.exception
        self.assertEqual((err.operation, err.status_code, err.retry_after, err.source, err.request_id),
                         ("hangup", 429, 60, "provider", "r1"))

    def test_valid_long_retry_after_is_not_shortened(self):
        err = client.ProviderTemporaryError("wait", retry_after=7200)
        self.assertEqual(err.retry_after, 7200)

    def test_local_budget_is_distinguishable_from_upstream_throttling(self):
        frappe.conf.vobiz_provider_reads_per_minute = 1
        policy.claim_provider_read("account")
        with self.assertRaises(client.ProviderTemporaryError) as result:
            policy.claim_provider_read("account")
        self.assertEqual(result.exception.source, "local_budget")
        self.assertIsNone(result.exception.status_code)

    def test_expired_worker_cannot_overwrite_replacement_retry_state(self):
        lock = MagicMock()
        lock.acquire.return_value = True
        lock.owned.return_value = False
        with patch.object(self.cache, "lock", return_value=lock):
            with policy.attempt("call"):
                self.cache.set_value(policy.state_key("call"), {"next_attempt": 9999})
                raise client.ProviderTemporaryError("late", retry_after=20)
        self.assertEqual(self.cache.get_value(policy.state_key("call")), {"next_attempt": 9999})


if __name__ == "__main__":
    unittest.main()
