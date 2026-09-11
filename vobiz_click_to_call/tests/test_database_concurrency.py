"""Opt-in local MariaDB tests; use only randomly named disposable tables.

VOBIZ_TEST_SITE=localhost VOBIZ_TEST_SITES_PATH=/path/to/bench/sites python -m
unittest vobiz_click_to_call.tests.test_database_concurrency
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import pymysql
from vobiz_click_to_call.services.callback_logging import append_callback
from vobiz_click_to_call.services.mapping_recovery import _recover_locked


class DatabaseAdapter:
    def __init__(self, connect, tables):
        self.connect = connect
        self.tables = tables
        self.local = threading.local()

    def sql(self, query, values=(), as_dict=False):
        for production, fixture in self.tables.items():
            query = query.replace(production, fixture)
        with self.local.connection.cursor() as cursor:
            cursor.execute(query, values)
            rows = cursor.fetchall()
            if as_dict:
                columns = [col[0] for col in cursor.description]
                return [frappe._dict(zip(columns, row)) for row in rows]
            return rows


@unittest.skipUnless(os.environ.get("VOBIZ_TEST_SITE"), "requires an explicitly selected local test site")
class DatabaseConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.init(site=os.environ["VOBIZ_TEST_SITE"], sites_path=os.environ["VOBIZ_TEST_SITES_PATH"])
        conf = frappe.conf
        cls.connection_options = dict(
            host=conf.db_host or "127.0.0.1", port=int(conf.db_port or 3306),
            user=conf.db_user or conf.db_name, password=conf.db_password,
            database=conf.db_name, charset="utf8mb4", autocommit=False,
        )
        if conf.db_socket:
            cls.connection_options["unix_socket"] = conf.db_socket
        cls.tables = {
            "tabVobiz Call Log": "vobiz_test_calls_" + uuid.uuid4().hex,
            "tabVobiz User Mapping": "vobiz_test_maps_" + uuid.uuid4().hex,
        }
        cls.admin = cls.connect()
        with cls.admin.cursor() as cursor:
            cursor.execute(f"""CREATE TABLE `{cls.tables['tabVobiz Call Log']}` (
                name VARCHAR(100) PRIMARY KEY, raw_callbacks LONGTEXT, raw_payload LONGTEXT,
                status VARCHAR(40), request_json LONGTEXT, modified DATETIME(6)) ENGINE=InnoDB""")
            cursor.execute(f"""CREATE TABLE `{cls.tables['tabVobiz User Mapping']}` (
                name VARCHAR(100) PRIMARY KEY, user VARCHAR(100), enabled INT,
                availability_status VARCHAR(40), auto_available_after_call INT,
                current_call_log VARCHAR(100), accept_calls INT, last_status_at DATETIME(6),
                modified DATETIME(6), modified_by VARCHAR(100)) ENGINE=InnoDB""")

    @classmethod
    def connect(cls):
        connection = pymysql.connect(**cls.connection_options)
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout=5")
            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return connection

    @classmethod
    def tearDownClass(cls):
        with cls.admin.cursor() as cursor:
            for table in cls.tables.values():
                if not table.startswith("vobiz_test_") or not table.replace("_", "").isalnum():
                    raise ValueError("Invalid test table name")
                cursor.execute(f"DROP TABLE `{table}`")
        cls.admin.close()
        frappe.destroy()

    def setUp(self):
        self.admin.rollback()
        with self.admin.cursor() as cursor:
            for table in self.tables.values():
                cursor.execute(f"DELETE FROM `{table}`")
            cursor.execute(f"""INSERT INTO `{self.tables['tabVobiz Call Log']}`
                VALUES ('CALL', '[]', '{{}}', 'Completed', '{{}}', '2026-09-11 10:00:00')""")
            cursor.execute(f"""INSERT INTO `{self.tables['tabVobiz User Mapping']}`
                (name, user, enabled, availability_status, auto_available_after_call, current_call_log, accept_calls)
                VALUES ('MAP', 'AGENT', 1, 'Busy', 1, 'CALL', 0)""")
        self.admin.commit()
        self.adapter = DatabaseAdapter(self.connect, self.tables)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(frappe, "db", self.adapter))
        self.stack.enter_context(patch.object(frappe, "session", SimpleNamespace(user="test-agent")))
        self.stack.enter_context(patch.object(frappe.utils, "now", return_value="2026-09-11 14:00:00"))
        self.stack.enter_context(patch.object(frappe, "publish_realtime"))

    def transaction(self, work):
        connection = self.connect()
        self.adapter.local.connection = connection
        try:
            work()
            connection.commit()
        finally:
            connection.rollback()
            connection.close()

    def fetch(self, query):
        self.admin.rollback()
        for production, fixture in self.tables.items():
            query = query.replace(production, fixture)
        with self.admin.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchall()

    def test_concurrent_history_appends_keep_every_event_and_call_state(self):
        barrier = threading.Barrier(12)

        def append(n):
            # Establish an old consistent-read snapshot before the locking read.
            self.adapter.sql("SELECT status FROM `tabVobiz Call Log` WHERE name='CALL'")
            barrier.wait(timeout=5)
            append_callback("CALL", str(n), {"sequence": n})

        with ThreadPoolExecutor(max_workers=12) as pool:
            jobs = [pool.submit(self.transaction, lambda n=n: append(n)) for n in range(12)]
            for job in jobs:
                job.result(timeout=15)
        raw, status, modified = self.fetch("SELECT raw_callbacks,status,modified FROM `tabVobiz Call Log`")[0]
        self.assertEqual({r["event"] for r in json.loads(raw)}, {str(n) for n in range(12)})
        self.assertEqual(status, "Completed")
        self.assertEqual(str(modified), "2026-09-11 10:00:00")

    def test_callback_holding_call_lock_cannot_deadlock_mapping_restore(self):
        callback_ready, mapping_locked = threading.Event(), threading.Event()

        def callback():
            append_callback("CALL", "hangup", {})
            callback_ready.set()
            self.assertTrue(mapping_locked.wait(5))

        def restore():
            self.assertTrue(callback_ready.wait(5))
            self.adapter.sql("SELECT name FROM `tabVobiz User Mapping` WHERE name='MAP' FOR UPDATE")
            mapping_locked.set()
            _recover_locked("MAP", "CALL")

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.transaction, callback), pool.submit(self.transaction, restore)]
            for job in jobs:
                job.result(timeout=15)
        self.assertEqual(self.fetch("SELECT current_call_log,availability_status FROM `tabVobiz User Mapping`")[0],
                         ("", "Available"))

    def test_old_recovery_waiting_on_new_call_preserves_new_reservation(self):
        reserved, restoring = threading.Event(), threading.Event()

        def new_call():
            self.adapter.sql("UPDATE `tabVobiz User Mapping` SET current_call_log='NEW' WHERE name='MAP'")
            reserved.set()
            self.assertTrue(restoring.wait(5))

        def old_callback():
            self.assertTrue(reserved.wait(5))
            restoring.set()
            _recover_locked("MAP", "CALL")

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.transaction, new_call), pool.submit(self.transaction, old_callback)]
            for job in jobs:
                job.result(timeout=15)
        self.assertEqual(self.fetch("SELECT current_call_log,availability_status FROM `tabVobiz User Mapping`")[0],
                         ("NEW", "Busy"))


if __name__ == "__main__":
    unittest.main()
