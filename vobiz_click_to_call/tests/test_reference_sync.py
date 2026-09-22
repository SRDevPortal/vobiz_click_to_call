from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch

import frappe
from vobiz_click_to_call.services import reference_sync as sync


class ReferenceSyncTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = datetime(2026, 9, 18, 12)
        self.db = MagicMock()
        self.db.sql.return_value = [(50,)]
        self.doc = frappe._dict(name="CALL", reference_doctype="CRM Lead",
            reference_name="LEAD", crm_lead="LEAD", patient=None,
            **{sync.TOKEN: "revision-1", sync.DUE: self.now})
        self.mock(frappe, "db", self.db)
        self.mock(frappe.utils, "now_datetime", MagicMock(return_value=self.now))
        self.mock(frappe, "get_doc", MagicMock(return_value=self.doc))
        self.mock(frappe, "log_error", MagicMock())
        self.mock(frappe, "get_traceback", MagicMock(return_value="Lock timeout"))
        self.enqueue = self.mock(frappe, "enqueue", MagicMock())
        self.cache = MagicMock()
        self.cache.lock.return_value = nullcontext()
        self.mock(frappe, "cache", MagicMock(return_value=self.cache))

    def mock(self, obj, key, value):
        return self.stack.enter_context(patch.object(obj, key, value))

    def test_pending_marker_is_transactional_and_enqueue_runs_only_after_commit(self):
        sync.request_reference_sync("CALL")
        self.enqueue.assert_not_called()
        self.db.commit.assert_not_called()
        args, kwargs = self.db.set_value.call_args
        self.assertEqual(args[:2], ("Vobiz Call Log", "CALL"))
        self.assertTrue(args[2][sync.TOKEN])
        self.assertEqual(args[2][sync.DUE], self.now)
        self.assertFalse(kwargs["update_modified"])
        self.db.after_commit.add.call_args.args[0]()
        self.assertEqual(self.enqueue.call_args.kwargs["queue"], "default")

    def test_queue_and_log_storage_failure_cannot_break_completed_request(self):
        self.enqueue.side_effect = ConnectionError("Redis down")
        self.mock(frappe, "logger", MagicMock(side_effect=OSError("Stale file handle")))
        sync.request_reference_sync("CALL")
        self.db.after_commit.add.call_args.args[0]()
        # Only the initial marker was written, never cleared on an enqueue error.
        self.assertEqual(self.db.set_value.call_count, 1)

    def test_duplicate_or_not_yet_due_job_does_no_summary_work(self):
        work = self.mock(sync, "_update_summaries", MagicMock())
        for token, due in [(None, self.now), ("r", self.now + timedelta(minutes=1))]:
            self.doc[sync.TOKEN], self.doc[sync.DUE] = token, due
            sync.sync_call_references("CALL")
        work.assert_not_called()
        self.db.commit.assert_not_called()

    def test_success_commits_lead_writes_before_conditional_acknowledgement(self):
        operations = []
        self.mock(sync, "_update_summaries", MagicMock(side_effect=lambda d: operations.append("summary")))
        self.db.commit.side_effect = lambda: operations.append("commit")
        def sql(q, values=None):
            if q.startswith("SELECT"): return [(50,)]
            if q.startswith("UPDATE"):
                self.assertEqual(operations, ["summary", "commit"])
                self.assertEqual(values, ("CALL", "revision-1"))
                self.assertIn(sync.TOKEN + "`=%s", q)
                operations.append("ack")
        self.db.sql.side_effect = sql
        sync.sync_call_references("CALL")
        self.assertEqual(operations, ["summary", "commit", "ack", "commit"])
        self.assertEqual(self.db.sql.call_args.args,
                         ("SET SESSION innodb_lock_wait_timeout=%s", (50,)))

    def test_blocked_lead_rolls_back_and_retries_without_changing_call_status(self):
        self.mock(sync, "_update_summaries", MagicMock(side_effect=RuntimeError("1205")))
        sync.sync_call_references("CALL")
        updates = [c.args for c in self.db.sql.call_args_list if c.args[0].startswith("UPDATE")]
        self.assertEqual(len(updates), 1)
        query, params = updates[0]
        self.assertNotIn("status", query)
        self.assertNotIn("Mapping", query)
        self.assertEqual(params, (self.now + timedelta(seconds=120), "CALL", "revision-1"))
        self.assertGreaterEqual(self.db.rollback.call_count, 2)

    def test_new_callback_revision_survives_old_worker_ack(self):
        stored = {sync.TOKEN: "revision-2", sync.DUE: self.now}
        def sql(q, values=None):
            if q.startswith("SELECT"): return [(50,)]
            if q.startswith("UPDATE") and stored[sync.TOKEN] == values[-1]:
                stored[sync.TOKEN] = None
        self.db.sql.side_effect = sql
        sync._acknowledge("CALL", "revision-1")
        self.assertEqual(stored[sync.TOKEN], "revision-2")

    def test_scheduler_recovers_committed_work_in_bounded_batches(self):
        self.mock(frappe, "get_all", MagicMock(return_value=["CALL-1", "CALL-2"]))
        sync.recover_pending_reference_syncs()
        self.assertEqual(self.enqueue.call_count, 2)
        self.assertEqual(frappe.get_all.call_args.kwargs["limit_page_length"], 100)

    def test_old_call_uses_latest_call_for_each_linked_summary(self):
        from vobiz_click_to_call.services import disposition
        from vobiz_ai.api import processing
        self.db.exists.return_value = True
        get_all = self.mock(frappe, "get_all", MagicMock(return_value=["NEW-CALL"]))
        latest = frappe._dict(name="NEW-CALL", crm_lead="LEAD", patient="PATIENT")
        self.mock(frappe, "get_doc", MagicMock(return_value=latest))
        metrics = self.mock(disposition, "update_reference_call_metrics", MagicMock())
        summaries = self.mock(processing, "_sync_linked_summaries", MagicMock())
        sync._update_summaries(self.doc)
        metrics.assert_called_once_with("CRM Lead", "LEAD")
        self.assertEqual(summaries.call_args.args[0].name, "NEW-CALL")
        self.assertIsNone(summaries.call_args.args[0].patient)
        self.assertEqual(get_all.call_args.kwargs["order_by"], "creation desc, name desc")


if __name__ == "__main__":
    unittest.main()
