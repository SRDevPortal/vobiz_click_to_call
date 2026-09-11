from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from vobiz_click_to_call.services import callback_logging as history
from vobiz_click_to_call.api import call
from vobiz_click_to_call.services import mapping_recovery as recovery
from vobiz_click_to_call.services import ai
from vobiz_click_to_call.services import cdr


class MappingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        for obj, name, value in (
            (frappe, "db", self.db),
            (frappe, "local", SimpleNamespace(flags=frappe._dict(in_test=False), request=None)),
            (frappe, "session", SimpleNamespace(user="test-agent")),
            (call, "_", lambda value: value),
            (frappe, "publish_realtime", MagicMock()),
            (frappe.utils, "now", lambda: "2026-09-11 14:00:00"),
        ):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def rows(self, pointer="CALL", status="Completed", availability="Busy", enabled=1, context=None):
        self.db.sql.side_effect = [
            [frappe._dict(name="MAP", user="AGENT", current_call_log=pointer, enabled=enabled,
                          availability_status=availability, auto_available_after_call=1)],
            [frappe._dict(name="CALL", status=status, request_json=json.dumps(context or {}),
                          modified="2026-09-11 13:00:00")],
            [],
        ]

    def test_restore_never_touches_mapping_in_call_transaction(self):
        with patch.object(recovery, "enqueue_recovery") as enqueue:
            call.restore_mapping_after_call("CALL")
        enqueue.assert_called_once_with("CALL")
        self.assertFalse(self.db.mock_calls)

    def test_restore_runs_only_after_commit(self):
        with patch.object(frappe, "enqueue") as enqueue:
            recovery.enqueue_recovery("CALL")
        self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

    def test_mapping_lock_precedes_call_lock_and_release_is_conditional(self):
        self.rows()
        recovery._recover_locked("MAP", "CALL")
        queries = [args.args[0] for args in self.db.sql.call_args_list]
        self.assertIn("tabVobiz User Mapping", queries[0])
        self.assertIn("FOR UPDATE", queries[0])
        self.assertIn("tabVobiz Call Log", queries[1])
        self.assertIn("FOR UPDATE", queries[1])
        self.assertIn("AND current_call_log=%s", queries[2])
        self.assertEqual(self.db.sql.call_args.args[1][:2], ("Available", 1))

    def test_old_callback_cannot_release_newer_call(self):
        self.rows(pointer="NEW-CALL")
        recovery._recover_locked("MAP", "CALL")
        self.assertEqual(self.db.sql.call_count, 1)

    def test_manual_offline_and_away_are_preserved(self):
        for status in ("Offline", "Away"):
            with self.subTest(status=status):
                self.db.reset_mock()
                self.rows(availability=status)
                recovery._recover_locked("MAP", "CALL")
                self.assertEqual(self.db.sql.call_args.args[1][:2], (status, 0))

    def test_disabled_agent_does_not_become_available(self):
        self.rows(enabled=0)
        recovery._recover_locked("MAP", "CALL")
        self.assertEqual(self.db.sql.call_args.args[1][:2], ("Away", 0))

    def test_browser_without_presence_is_not_made_available(self):
        self.rows(context={"source": "vobiz_system_call", "call_device": "Browser Softphone"})
        with patch("vobiz_system_call.api.lifecycle.presence", return_value=None):
            recovery._recover_locked("MAP", "CALL")
        self.assertEqual(self.db.sql.call_args.args[1][:2], ("Away", 0))

    def test_missing_log_releases_only_its_current_pointer(self):
        self.rows()
        mapping = self.db.sql.side_effect.__next__()
        self.db.sql.side_effect = [mapping, [], []]
        recovery._recover_locked("MAP", "CALL")
        self.assertIn("AND current_call_log=%s", self.db.sql.call_args.args[0])

    def test_unconfirmed_call_is_reconciled_not_released_by_age(self):
        self.rows(status="Confirmation Pending")
        with patch.object(frappe, "enqueue") as enqueue:
            recovery._recover_locked("MAP", "CALL")
        self.assertEqual(self.db.sql.call_count, 2)
        self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

    def test_not_found_cdr_does_not_create_immediate_job_loop(self):
        self.db.get_value.return_value = "Confirmation Pending"
        with patch("vobiz_click_to_call.services.cdr.sync_call_log_cdr"), patch.object(recovery, "enqueue_recovery") as enqueue:
            recovery.reconcile_legacy_call("CALL")
        enqueue.assert_not_called()

    def test_deadlock_requests_whole_job_retry(self):
        self.db.sql.side_effect = frappe.QueryDeadlockError("deadlock")
        with self.assertRaises(frappe.RetryBackgroundJobError):
            recovery.recover_mapping("CALL", "MAP")
        self.db.commit.assert_not_called()

    def test_hook_does_not_swallow_database_failure(self):
        with patch.object(call, "restore_mapping_after_call", side_effect=frappe.QueryDeadlockError("deadlock")):
            with self.assertRaises(frappe.QueryDeadlockError):
                ai.restore_mapping_for_call_log("CALL")

    def test_history_locks_latest_array_without_document_save_or_status_write(self):
        old = [{"event": str(n)} for n in range(50)]
        self.db.sql.side_effect = [[(json.dumps(old),)], []]
        with patch.object(frappe, "get_doc") as get_doc:
            history.append_callback("CALL", "hangup", {"token": "secret", "cmd": "method", "status": "done"})
        get_doc.assert_not_called()
        self.assertIn("FOR UPDATE", self.db.sql.call_args_list[0].args[0])
        query, values = self.db.sql.call_args.args
        self.assertNotIn("modified", query)
        self.assertNotIn("status", query)
        rows = json.loads(values[0])
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0]["event"], "1")
        self.assertEqual(rows[-1]["payload"], {"status": "done"})

    def test_history_database_conflict_retries_worker(self):
        self.db.sql.side_effect = frappe.QueryDeadlockError("deadlock")
        with self.assertRaises(frappe.RetryBackgroundJobError):
            history.append_callback("CALL", "hangup", {})

    def test_missing_and_malformed_history(self):
        self.db.sql.return_value = []
        history.append_callback("MISSING", "ring", {})
        self.assertEqual(self.db.sql.call_count, 1)
        for value in ("{bad-json", "{}", "null"):
            self.db.sql.side_effect = [[(value,)], []]
            history.append_callback("CALL", "ring", {})
            self.assertEqual(len(json.loads(self.db.sql.call_args.args[1][0])), 1)

    def test_recovery_never_matches_a_different_call_to_the_same_customer(self):
        doc = frappe._dict(call_uuid="correct", customer_number="919876543210")
        response = {"data": [{"uuid": "wrong", "to": doc.customer_number, "status": "completed"}]}
        self.assertIsNone(cdr.find_matching_cdr(doc, response, strict_match=True))
        self.assertIsNone(cdr.find_matching_cdr(doc, response))
        doc.call_uuid = ""
        self.assertIsNone(cdr.find_matching_cdr(doc, response, strict_match=True))

    def test_strict_recovery_requires_terminal_provider_evidence(self):
        self.assertFalse(cdr._terminal_cdr({"uuid": "correct", "status": "ringing"}))
        self.assertFalse(cdr._terminal_cdr({"uuid": "correct"}))
        self.assertTrue(cdr._terminal_cdr({"uuid": "correct", "status": "completed"}))

    def test_conflicting_ring_cannot_reopen_completed_call(self):
        from vobiz_click_to_call.services.call_log_update import save_doc_latest
        stale = MagicMock(doctype="Vobiz Call Log")
        stale.name = "CALL"
        stale.save.side_effect = frappe.TimestampMismatchError()
        stale.get.side_effect = {"status": "Ringing", "call_status": "ring", "recording_url": "recording"}.get
        latest = MagicMock()
        latest.get.side_effect = {"status": "Completed"}.get
        with patch.object(frappe, "get_doc", return_value=latest) as get_doc:
            result = save_doc_latest(stale, {"status": "Queued", "call_status": "queued", "recording_url": ""})
        get_doc.assert_called_once_with("Vobiz Call Log", "CALL", for_update=True)
        latest.set.assert_called_once_with("recording_url", "recording")
        self.assertIs(result, latest)

    def test_age_does_not_make_an_unconfirmed_call_available(self):
        self.db.exists.return_value = True
        self.db.get_value.return_value = frappe._dict(status="Confirmation Pending", modified="2020-01-01")
        self.assertTrue(call.is_active_call_log("CALL"))

    def test_both_initial_and_fallback_read_timeout_remain_pending_without_retry(self):
        from requests.exceptions import ReadTimeout
        for errors in ([ReadTimeout("slow")], [Exception("From number not owned"), ReadTimeout("slow")]):
            with self.subTest(attempts=len(errors)), ExitStack() as stack:
                doc = MagicMock(name="call_doc")
                doc.name, doc.callback_token, doc.status = "CALL", "callback-secret", "Confirmation Pending"
                replacements = {
                    "get_settings": frappe._dict(enabled=1, default_call_flow="Agent First"),
                    "get_allowed_doctypes": {"CRM Lead"},
                    "get_user_mapping": {"name": "MAP", "agent_mobile": "+919876543210"},
                    "get_mapping_unavailable_reason": "", "get_default_country_code": "+91",
                    "resolve_target_number": ("+919876543211", "mobile_no"),
                    "assert_call_allowed": None, "create_call_log": doc, "mark_mapping_busy": None,
                    "snapshot_doc": {}, "save_doc_latest": doc, "log_vobiz_event": None,
                    "build_callback_url": "https://example.test/callback", "_mark_confirmation_pending": True,
                }
                for name, result in replacements.items():
                    stack.enter_context(patch.object(call, name, return_value=result))
                stack.enter_context(patch.object(call, "get_caller_id", side_effect=["+919000000001", "+919000000002"]))
                stack.enter_context(patch.object(frappe, "get_doc", return_value=MagicMock()))
                client = stack.enter_context(patch.object(call, "VobizClient"))
                client.return_value.make_call.side_effect = errors
                fail = stack.enter_context(patch.object(call, "_fail_provider_call"))
                result = call.start_call("CRM Lead", "LEAD")
                self.assertTrue(result["confirmation_pending"])
                self.assertEqual(client.return_value.make_call.call_count, len(errors))
                fail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
