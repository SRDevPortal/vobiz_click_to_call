"""Real InnoDB contention tests, isolated to a disposable local test table."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
import threading
import time
import unittest
import uuid
from unittest.mock import patch

import frappe
import pymysql
import redis
from werkzeug.local import LocalProxy
from werkzeug.wrappers import Response

from vobiz_click_to_call.services import incoming_routing as routing


@unittest.skipUnless(os.environ.get("VOBIZ_TEST_SITE"), "requires explicit local database")
class IncomingReservationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.init(site=os.environ["VOBIZ_TEST_SITE"],
                    sites_path=os.environ["VOBIZ_TEST_SITES_PATH"])
        conf = frappe.conf
        cls.redis_url = conf.redis_cache
        cls.options = dict(host=conf.db_host or "127.0.0.1", port=int(conf.db_port or 3306),
                           user=conf.db_user or conf.db_name, password=conf.db_password,
                           database=conf.db_name, autocommit=False)
        if conf.db_socket:
            cls.options["unix_socket"] = conf.db_socket
        cls.table = "vobiz_test_incoming_" + uuid.uuid4().hex
        cls.admin = pymysql.connect(**cls.options)
        with cls.admin.cursor() as c:
            c.execute(f"""CREATE TABLE {cls.table} (
                user VARCHAR(140) PRIMARY KEY, enabled INT, accept_calls INT,
                availability_status VARCHAR(40), current_call_log VARCHAR(140),
                last_status_at DATETIME(6), modified DATETIME(6)) ENGINE=InnoDB""")

    @classmethod
    def tearDownClass(cls):
        cls.admin.rollback()
        assert cls.table.startswith("vobiz_test_incoming_") and cls.table.replace("_", "").isalnum()
        with cls.admin.cursor() as c:
            c.execute("DROP TABLE " + cls.table)
        cls.admin.close()
        frappe.destroy()

    def setUp(self):
        self.local = threading.local()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(frappe, "flags", frappe._dict()))
        self.stack.enter_context(patch.object(frappe, "db", self))
        self.stack.enter_context(patch.object(frappe.utils, "now", lambda: "2026-09-22 12:00:00"))
        with self.admin.cursor() as c:
            c.execute("DELETE FROM " + self.table)
            c.executemany(f"INSERT INTO {self.table} (user,enabled,accept_calls,availability_status,current_call_log)"
                          " VALUES (%s,1,1,'Available','')", [(f"agent-{i}",) for i in range(20)])
        self.admin.commit()

    def sql(self, query, args=None, **kwargs):
        query = query.replace("`tabVobiz User Mapping`", self.table)
        with self.local.connection.cursor() as c:
            c.execute(query, args)
            return c.fetchall()

    def rollback(self):
        self.local.connection.rollback()

    def claim(self, user, call, barrier=None):
        self.local.connection = pymysql.connect(**self.options)
        try:
            with self.local.connection.cursor() as c:
                c.execute("SET SESSION innodb_lock_wait_timeout=2")
            if barrier:
                barrier.wait(timeout=5)
            routing.reserve_agent(user, call)
            self.local.connection.commit()
            return "reserved"
        except routing.RouteContended:
            self.local.connection.rollback()
            return "contended"
        except pymysql.err.OperationalError as exc:
            self.local.connection.rollback()
            return exc.args[0]
        finally:
            self.local.connection.close()

    def test_twenty_simultaneous_agents_can_reserve_independently(self):
        barrier = threading.Barrier(20)
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda i: self.claim(f"agent-{i}", f"call-{i}", barrier), range(20)))
        self.assertEqual(results, ["reserved"] * 20)

    def test_two_callers_cannot_reserve_same_agent(self):
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.claim, "agent-0", call, barrier) for call in ["CALL-A", "CALL-B"]]
            self.assertCountEqual([f.result() for f in futures], ["reserved", "contended"])
        with self.admin.cursor() as c:
            c.execute(f"SELECT current_call_log FROM {self.table} WHERE user='agent-0'")
            self.assertIn(c.fetchone()[0], ["CALL-A", "CALL-B"])

    def test_busy_outgoing_agent_cannot_be_overwritten(self):
        with self.admin.cursor() as c:
            c.execute(f"UPDATE {self.table} SET current_call_log='OUTGOING',accept_calls=0,"
                      "availability_status='Busy' WHERE user='agent-0'")
        self.admin.commit()
        self.assertEqual(self.claim("agent-0", "INCOMING"), "contended")

    def test_locked_agent_times_out_but_other_agent_progresses(self):
        with self.admin.cursor() as c:
            c.execute(f"SELECT user FROM {self.table} WHERE user='agent-0' FOR UPDATE")
        with ThreadPoolExecutor(max_workers=2) as pool:
            blocked = pool.submit(self.claim, "agent-0", "BLOCKED")
            other = pool.submit(self.claim, "agent-1", "INDEPENDENT")
            self.assertEqual(other.result(timeout=1.5), "reserved")
            self.assertEqual(blocked.result(timeout=4), 1205)
        self.admin.rollback()

    def test_disabled_or_nonaccepting_agent_cannot_be_reserved(self):
        with self.admin.cursor() as c:
            c.execute(f"UPDATE {self.table} SET enabled=0 WHERE user='agent-0'")
            c.execute(f"UPDATE {self.table} SET accept_calls=0 WHERE user='agent-1'")
        self.admin.commit()
        self.assertEqual(self.claim("agent-0", "CALL"), "contended")
        self.assertEqual(self.claim("agent-1", "CALL"), "contended")

    def routing_pair(self, second_caller, second_uuid):
        client = redis.Redis.from_url(self.redis_url)
        prefix = "vobiz-test-incoming:" + uuid.uuid4().hex + ":"
        class Cache:
            def make_key(self, key):
                return prefix + key
            def lock(self, key, **kwargs):
                return client.lock(key, **kwargs)
            def get_value(self, key, **kwargs):
                return client.get(self.make_key(key))
        cache = Cache()
        local_flags = threading.local()
        def flags():
            if not hasattr(local_flags, "value"):
                local_flags.value = frappe._dict()
            return local_flags.value
        self.stack.enter_context(patch.object(frappe, "flags", LocalProxy(flags)))
        self.stack.enter_context(patch.object(frappe, "cache", lambda: cache))
        self.stack.enter_context(patch.object(frappe, "logger"))
        entered, release = threading.Event(), threading.Event()
        handled = []

        def request(caller, call_uuid, hold):
            self.local.connection = pymysql.connect(**self.options)
            try:
                def handler():
                    handled.append(call_uuid)
                    if hold:
                        entered.set()
                        if not release.wait(timeout=5):
                            raise AssertionError("peer did not progress")
                    return Response("<Response><Dial/></Response>")
                return routing.run(
                    {"From": caller, "To": "+919262175560", "CallUUID": call_uuid, "token": "test"},
                    handler, "https://example.test/answer",
                ).get_data(as_text=True)
            finally:
                self.local.connection.rollback()
                self.local.connection.close()

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(request, "+919873090386", "CALL-A", True)
                self.assertTrue(entered.wait(timeout=2))
                try:
                    second = pool.submit(request, second_caller, second_uuid, False).result(timeout=2)
                finally:
                    release.set()
                self.assertIn("<Dial", first.result(timeout=2))
            self.assertEqual(list(client.scan_iter(match=prefix + "*")), [])
            return second, handled
        finally:
            client.close()

    def test_same_did_different_callers_do_not_serialize_on_real_redis(self):
        second, handled = self.routing_pair("+919466073244", "CALL-B")
        self.assertIn("<Dial", second)
        self.assertEqual(handled, ["CALL-A", "CALL-B"])

    def test_same_caller_across_calls_retries_without_duplicate_routing(self):
        second, handled = self.routing_pair("+919873090386", "CALL-B")
        self.assertIn("<Redirect", second)
        self.assertEqual(handled, ["CALL-A"])

    def test_same_provider_uuid_retries_without_duplicate_routing(self):
        second, handled = self.routing_pair("+919466073244", "CALL-A")
        self.assertIn("<Redirect", second)
        self.assertEqual(handled, ["CALL-A"])
