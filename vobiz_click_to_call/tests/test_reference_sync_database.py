"""Opt-in lock regression tests on disposable local MariaDB tables only."""
from contextlib import ExitStack, nullcontext
from datetime import datetime
import os
import unittest
import uuid
from unittest.mock import MagicMock, patch

import frappe
import pymysql

from vobiz_click_to_call.services import reference_sync as sync
from vobiz_system_call.api import lifecycle


@unittest.skipUnless(os.environ.get("VOBIZ_TEST_SITE"), "requires explicit local database")
class ReferenceSyncDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.init(site=os.environ["VOBIZ_TEST_SITE"],
                    sites_path=os.environ["VOBIZ_TEST_SITES_PATH"])
        conf = frappe.conf
        cls.options = dict(host=conf.db_host or "127.0.0.1", port=int(conf.db_port or 3306),
            user=conf.db_user or conf.db_name, password=conf.db_password,
            database=conf.db_name, charset="utf8mb4", autocommit=False)
        if conf.db_socket: cls.options["unix_socket"] = conf.db_socket
        cls.tables = {k: "vobiz_test_ref_" + uuid.uuid4().hex for k in
                      ["tabVobiz Call Log", "tabVobiz User Mapping", "tabCRM Lead"]}
        cls.admin = pymysql.connect(**cls.options)
        with cls.admin.cursor() as c:
            c.execute(f"CREATE TABLE {cls.tables['tabVobiz Call Log']} (name VARCHAR(100) PRIMARY KEY,"
                " status VARCHAR(40),end_time DATETIME(6),call_status VARCHAR(80),hangup_cause VARCHAR(140),"
                "duration DOUBLE,vobiz_reference_sync_token VARCHAR(100),vobiz_reference_sync_due DATETIME(6)) ENGINE=InnoDB")
            c.execute(f"CREATE TABLE {cls.tables['tabVobiz User Mapping']} (name VARCHAR(100) PRIMARY KEY,"
                "current_call_log VARCHAR(100),availability_status VARCHAR(40),accept_calls INT,last_status_at DATETIME(6)) ENGINE=InnoDB")
            c.execute(f"CREATE TABLE {cls.tables['tabCRM Lead']} (name VARCHAR(100) PRIMARY KEY,"
                "vobiz_last_call_status VARCHAR(40)) ENGINE=InnoDB")

    @classmethod
    def tearDownClass(cls):
        cls.admin.rollback()
        with cls.admin.cursor() as c:
            for table in cls.tables.values():
                if not table.startswith("vobiz_test_ref_") or not table.replace("_", "").isalnum():
                    raise ValueError("Unexpected test table")
                c.execute(f"DROP TABLE {table}")
        cls.admin.close()
        frappe.destroy()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.connection = pymysql.connect(**self.options)
        self.addCleanup(self.connection.close)
        self.blocker = pymysql.connect(**self.options)
        self.addCleanup(self.blocker.close)
        for table in self.tables.values():
            with self.admin.cursor() as c: c.execute(f"DELETE FROM {table}")
        with self.admin.cursor() as c:
            c.execute(f"INSERT INTO {self.tables['tabVobiz Call Log']} (name,status) VALUES ('CALL','Connected')")
            c.execute(f"INSERT INTO {self.tables['tabVobiz User Mapping']} "
                      "(name,current_call_log,availability_status,accept_calls) VALUES ('MAP','CALL','Busy',0)")
            c.execute(f"INSERT INTO {self.tables['tabCRM Lead']} VALUES ('LEAD','Connected')")
        self.admin.commit()
        self.callbacks = []
        self.row = frappe._dict(name="CALL", user="AGENT",status="Connected",
            answer_time=datetime(2026,9,18,10), end_time=None,
            reference_doctype="CRM Lead",reference_name="LEAD",crm_lead="LEAD",patient=None,
            request_json='{"source":"vobiz_system_call","call_device":"Browser Softphone"}')
        self.mapping = frappe._dict(name="MAP",user="AGENT", current_call_log="CALL",
            availability_status="Busy",auto_available_after_call=1)
        db = MagicMock()
        db.sql.side_effect = self.sql
        db.set_value.side_effect = self.set_value
        db.commit.side_effect = self.commit
        db.rollback.side_effect = self.connection.rollback
        db.after_commit.add.side_effect = self.callbacks.append
        self.mock(frappe, "db", db)
        self.mock(frappe, "get_doc", self.get_doc)
        self.mock(frappe.utils, "now_datetime", lambda: datetime(2026,9,18,10,1))
        self.mock(frappe, "publish_realtime", MagicMock())
        self.mock(frappe, "log_error", MagicMock())
        self.mock(frappe, "get_traceback", lambda: "fixture lead locked")
        self.enqueue = self.mock(frappe, "enqueue", MagicMock())
        self.mock(lifecycle, "presence", lambda user: True)
        cache = MagicMock()
        cache.lock.return_value = nullcontext()
        self.mock(frappe, "cache", lambda: cache)

    def mock(self, obj, name, value):
        return self.stack.enter_context(patch.object(obj, name, value))

    def sql(self, query, values=None, **kwargs):
        for production, fixture in self.tables.items():
            query = query.replace(production, fixture)
        with self.connection.cursor() as c:
            c.execute(query, values)
            return c.fetchall()

    def set_value(self, dt, name, values, **kwargs):
        query = "UPDATE " + self.tables["tab" + dt] + " SET " + ",".join(k+"=%s" for k in values) + " WHERE name=%s"
        self.sql(query, tuple(values.values())+(name,))

    def commit(self):
        self.connection.commit()
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks: callback()

    def get_doc(self, dt, name):
        with self.connection.cursor(pymysql.cursors.DictCursor) as c:
            c.execute(f"SELECT * FROM {self.tables['tab' + dt]} WHERE name=%s", (name,))
            return frappe._dict(dict(self.row) | c.fetchone())

    def lock_lead(self):
        with self.blocker.cursor() as c:
            c.execute(f"SELECT name FROM {self.tables['tabCRM Lead']} WHERE name='LEAD' FOR UPDATE")

    def test_completion_and_disposition_event_survive_locked_lead_then_summary_retries(self):
        self.lock_lead()
        self.sql("SELECT name FROM tabVobiz User Mapping WHERE name='MAP' FOR UPDATE".replace(
            "tabVobiz User Mapping", self.tables["tabVobiz User Mapping"]))
        self.sql(f"SELECT name FROM {self.tables['tabVobiz Call Log']} WHERE name='CALL' FOR UPDATE")
        # Runs actual production completion and mapping release; lead stays locked.
        self.assertEqual(lifecycle.finish_locked(self.mapping, self.row, "provider-hangup",
                                                status="Completed"), "Completed")
        self.commit()
        with self.admin.cursor() as c:
            c.execute(f"SELECT status,vobiz_reference_sync_token FROM {self.tables['tabVobiz Call Log']}")
            status, token = c.fetchone()
            self.assertEqual(status, "Completed")
            self.assertTrue(token)
            c.execute(f"SELECT current_call_log,availability_status FROM {self.tables['tabVobiz User Mapping']}")
            self.assertEqual(c.fetchone(), ("", "Available"))
        self.admin.rollback()
        self.assertTrue(frappe.publish_realtime.call_args.kwargs["after_commit"])
        def update(doc):
            self.sql(f"UPDATE {self.tables['tabCRM Lead']} SET vobiz_last_call_status='Completed' WHERE name='LEAD'")
        self.mock(sync, "_update_summaries", update)
        # Real 5-second InnoDB timeout, isolated to worker transaction.
        sync.sync_call_references("CALL")
        doc = self.get_doc("Vobiz Call Log", "CALL")
        self.assertEqual(doc.status, "Completed")
        self.assertEqual(doc[sync.TOKEN], token)
        self.assertGreater(doc[sync.DUE], frappe.utils.now_datetime())
        self.connection.rollback()
        self.blocker.rollback()
        self.sql(f"UPDATE {self.tables['tabVobiz Call Log']} SET {sync.DUE}='2026-09-18 10:00:00'")
        self.commit()
        sync.sync_call_references("CALL")
        self.assertIsNone(self.get_doc("Vobiz Call Log", "CALL")[sync.TOKEN])
        self.assertEqual(self.sql(f"SELECT vobiz_last_call_status FROM {self.tables['tabCRM Lead']}")[0][0],
                         "Completed")
        self.connection.rollback()
