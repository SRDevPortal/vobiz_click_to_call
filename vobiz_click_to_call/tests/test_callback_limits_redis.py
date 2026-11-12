"""Real Redis tests; launch a private Unix-socket server, never a site's Redis.

Run using the bench virtualenv's Python unittest runner. Requires redis-server.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import shutil
import subprocess
import time
import unittest
import uuid

import frappe
import redis
from frappe.utils.redis_wrapper import RedisWrapper
from vobiz_click_to_call.services import callback_limits as limits


@unittest.skipUnless(shutil.which("redis-server"), "redis-server is required for integration tests")
class CallbackRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix="vobiz-limits-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.socket = str(Path(cls.temp.name) / "redis.sock")
        cls.server = subprocess.Popen([
            "redis-server", "--port", "0", "--unixsocket", cls.socket,
            "--unixsocketperm", "700", "--save", "", "--appendonly", "no",
            "--dir", cls.temp.name, "--loglevel", "warning",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop_server)
        probe = redis.Redis(unix_socket_path=cls.socket)
        cls.addClassCleanup(probe.close)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not Path(cls.socket).exists():
                time.sleep(0.01)
                continue
            try:
                if probe.ping():
                    return
            except redis.ConnectionError:
                time.sleep(0.01)
        raise RuntimeError("Isolated Redis did not start")

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=5)

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.cache = RedisWrapper(unix_socket_path=self.socket)
        self.addCleanup(self.cache.close)
        self.site = "callback_test_" + uuid.uuid4().hex
        def reject(message, exc=ValueError):
            raise exc(message)
        for name, value in {
            "cache": self.cache, "request": SimpleNamespace(method="POST"),
            "local": SimpleNamespace(request_ip="192.0.2.1"),
            "form_dict": frappe._dict(cmd=limits.CALLBACK_COMMANDS[0]),
            "conf": frappe._dict(db_name=self.site), "throw": reject,
            "_": lambda value: value, "logger": MagicMock(),
        }.items():
            stack.enter_context(patch.object(frappe, name, value))
        self.key = self.cache.make_key(f"rl:{frappe.form_dict.cmd}:192.0.2.1")

    def wait_expired(self, key):
        deadline = time.monotonic() + 3
        while self.cache.execute_command("EXISTS", key) and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertFalse(self.cache.execute_command("EXISTS", key), "Counter did not expire")

    def test_new_counter_and_exact_limit_boundary(self):
        handler = limits.callback_rate_limit("answer", default=2)(lambda: "OK")
        self.assertEqual(handler(), "OK")
        self.assertGreater(self.cache.pttl(self.key), 0)
        self.assertLessEqual(self.cache.pttl(self.key), 60000)
        self.assertEqual(handler(), "OK")
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()
        self.assertEqual(int(self.cache.get(self.key)), 3)

    def test_stuck_counter_keeps_count_repairs_expiry_and_recovers_next_window(self):
        self.cache.set(self.key, 16654)
        frappe.conf.vobiz_answer_requests_per_minute = 6000
        handler = limits.callback_rate_limit("answer")(lambda: "OK")
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()
        self.assertEqual(int(self.cache.get(self.key)), 16655)
        self.assertGreater(self.cache.pttl(self.key), 0)
        events = [c.args[0]["event"] for c in frappe.logger.return_value.warning.call_args_list]
        self.assertIn("callback_counter_expiry_repaired", events)
        self.cache.pexpire(self.key, 5)
        self.wait_expired(self.key)
        self.assertEqual(handler(), "OK")
        self.assertEqual(int(self.cache.get(self.key)), 1)

    def test_existing_positive_and_subsecond_expiry_are_not_extended(self):
        self.cache.set(self.key, 10, px=800)
        for _ in range(40):
            _, ttl, repaired = limits._count_callback(self.cache, self.key)
            self.assertEqual(repaired, 0)
            self.assertGreater(ttl, 0)
            self.assertLessEqual(ttl, 800)
        self.cache.pexpire(self.key, 200)
        self.assertEqual(self.cache.ttl(self.key), 0)
        _, ttl, repaired = limits._count_callback(self.cache, self.key)
        self.assertEqual(repaired, 0)
        self.assertLessEqual(ttl, 200)

    def test_simultaneous_requests_have_no_lost_counts(self):
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: limits._count_callback(self.cache, self.key), range(160)))
        self.assertEqual(sorted(r[0] for r in results), list(range(1, 161)))
        self.assertTrue(all(r[1] > 0 for r in results))
        self.assertEqual(int(self.cache.get(self.key)), 160)
        self.assertGreater(self.cache.pttl(self.key), 0)

    def test_concurrent_limit_enforcement_allows_exactly_the_configured_number(self):
        handler = limits.callback_rate_limit("answer", default=60)(lambda: True)
        def request(_):
            try:
                return handler()
            except frappe.RateLimitExceededError:
                return False
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(request, range(160)))
        self.assertEqual(sum(results), 60)
        self.assertEqual(int(self.cache.get(self.key)), 160)
        self.assertGreater(self.cache.pttl(self.key), 0)

    def test_persistent_counter_under_limit_is_repaired_without_resetting_count(self):
        self.cache.set(self.key, 3)
        handler = limits.callback_rate_limit("answer", default=4)(lambda: "OK")
        self.assertEqual(handler(), "OK")
        self.assertEqual(int(self.cache.get(self.key)), 4)
        self.assertGreater(self.cache.pttl(self.key), 0)
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()

    def test_concurrent_requests_across_expiry_always_keep_a_timer(self):
        self.cache.set(self.key, 700, px=5)
        def request(index):
            if index % 2:
                time.sleep(0.012)
            return limits._count_callback(self.cache, self.key)
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(request, range(160)))
        self.assertTrue(all(r[1] >= 0 for r in results))
        self.assertGreater(self.cache.pttl(self.key), 0)
        # At least some traffic entered a new window after the short old expiry.
        self.assertTrue(any(r[0] < 700 for r in results))

    def test_legacy_get_increment_expiry_gap_is_reproducible(self):
        from frappe.rate_limiter import rate_limit
        self.cache.set(self.key, 4, px=10000)
        original_get = self.cache.get
        def read_then_expire(key):
            value = original_get(key)
            self.cache.pexpire(key, 1)
            self.wait_expired(key)
            return value
        # Deterministically reproduce expiry between the legacy GET and INCR.
        with patch.object(self.cache, "get", side_effect=read_then_expire):
            self.assertEqual(rate_limit(limit=10, seconds=60)(lambda: "OK")(), "OK")
        self.assertEqual(self.cache.pttl(self.key), -1)
        count, ttl, repaired = limits._count_callback(self.cache, self.key)
        self.assertEqual((count, repaired), (2, 1))
        self.assertGreater(ttl, 0)

    def test_site_endpoint_and_ip_are_independent(self):
        handler = limits.callback_rate_limit("answer", default=1)(lambda: "OK")
        handler()
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()
        frappe.local.request_ip = "192.0.2.2"
        self.assertEqual(handler(), "OK")
        frappe.form_dict.cmd = limits.CALLBACK_COMMANDS[1]
        self.assertEqual(handler(), "OK")
        frappe.local.request_ip = "192.0.2.1"
        frappe.form_dict.cmd = limits.CALLBACK_COMMANDS[0]
        frappe.conf.db_name = self.site + "_other"
        self.assertEqual(handler(), "OK")
        frappe.conf.db_name = self.site
        with self.assertRaises(frappe.RateLimitExceededError):
            handler()

    def test_nested_callbacks_count_once_but_still_authenticate(self):
        @limits.callback_rate_limit("event", default=1)
        def inner():
            raise PermissionError("callback authentication failed")
        @limits.callback_rate_limit("answer", default=2)
        def outer():
            return inner()
        for _ in range(2):
            with self.assertRaises(PermissionError):
                outer()
        with self.assertRaises(frappe.RateLimitExceededError):
            outer()
        self.assertEqual(int(self.cache.get(self.key)), 3)
        self.assertFalse(limits._active.get())

    def test_migration_repairs_only_selected_site_callback_keys_and_is_idempotent(self):
        from vobiz_click_to_call.patches.v1_0.repair_callback_counter_expiry import execute
        keys = [self.cache.make_key(f"rl:{cmd}:192.0.2.1") for cmd in limits.CALLBACK_COMMANDS]
        for key in keys:
            self.cache.set(key, 16654)
        healthy = self.cache.make_key(f"rl:{limits.CALLBACK_COMMANDS[0]}:192.0.2.2")
        self.cache.set(healthy, 6, px=10000)
        excluded = [
            (self.site + "_other|rl:" + limits.CALLBACK_COMMANDS[0] + ":192.0.2.1").encode(),
            self.cache.make_key("rl:vobiz_click_to_call.api.console.record_agent_activity:192.0.2.1"),
            self.cache.make_key("rl:vobiz_system_call.api.webrtc.answer_extra:192.0.2.1"),
            self.cache.make_key("session:test"),
        ]
        for key in excluded:
            self.cache.set(key, 123)
        result = execute()
        self.assertEqual(result["repaired"], len(keys))
        for key in keys:
            self.assertEqual(int(self.cache.get(key)), 16654)
            self.assertGreater(self.cache.pttl(key), 0)
        for key in excluded:
            self.assertEqual(self.cache.pttl(key), -1)
        self.assertLessEqual(self.cache.pttl(healthy), 10000)
        self.assertEqual(execute()["repaired"], 0)

    def test_migration_escapes_site_namespace_globs(self):
        frappe.conf.db_name = self.site + "[x]*?"
        key = self.cache.make_key(f"rl:{limits.CALLBACK_COMMANDS[0]}:192.0.2.1")
        self.cache.set(key, 10)
        result = limits.repair_callback_counter_expiry()
        self.assertEqual(result["repaired"], 1)
        self.assertGreater(self.cache.pttl(key), 0)
