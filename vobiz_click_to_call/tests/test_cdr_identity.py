from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from vobiz_click_to_call.services import cdr


class CDRIdentityTests(unittest.TestCase):
    def setUp(self):
        self.doc = frappe._dict(name="CALL", user="agent", status="Failed", duration=0,
                               billsec=0, customer_number="+919873090386", recording_url="",
                               recording_status="Not Started", cdr_json="original")
        self.doc.save = MagicMock()
        self.provider = MagicMock()
        self.db = MagicMock()
        replacements = [
            (frappe, "local", SimpleNamespace(flags=frappe._dict(in_test=False))),
            (frappe, "db", self.db), (frappe, "session", SimpleNamespace(user="agent")),
            (frappe, "get_roles", lambda: []), (frappe, "get_doc", lambda *args, **kwargs: self.doc),
            (frappe, "throw", lambda message: (_ for _ in ()).throw(ValueError(message))),
            (frappe.utils, "now", lambda: "2026-09-14 16:45:00"),
            (cdr, "_", lambda text: text),
            (cdr, "get_settings", lambda: frappe._dict(enabled=1, enable_cdr_sync=1)),
            (cdr, "VobizClient", lambda *args: self.provider),
            (cdr, "update_reference_call_metrics", MagicMock()),
            (cdr, "sync_linked_summaries", MagicMock()),
        ]
        for obj, name, value in replacements:
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_default_and_strict_sync_without_provider_id_do_not_query_or_mutate(self):
        for strict in (False, True):
            result = cdr.sync_call_log_cdr("CALL", strict_match=strict)
            self.assertEqual(result["status"], "Awaiting Provider ID")
        self.provider.search_cdrs.assert_not_called()
        self.doc.save.assert_not_called()
        self.assertEqual(self.doc.status, "Failed")
        self.assertEqual(self.doc.cdr_json, "original")

    def test_same_phone_and_time_cannot_identify_a_cdr_without_provider_id(self):
        response = {"data": [{"uuid": "later-call", "destination_number": self.doc.customer_number,
                              "start_time": "2026-09-14 12:28:04", "billsec": 22}]}
        self.assertIsNone(cdr.find_matching_cdr(self.doc, response))

    def test_browser_event_uuid_is_not_promoted_to_trusted_provider_identity(self):
        self.doc.raw_callbacks = json.dumps([{"event": "onCallFailed", "payload": {"call_uuid": "browser-uuid"}}])
        self.assertIsNone(cdr.find_matching_cdr(self.doc, {"data": [{"uuid": "browser-uuid"}]}))

    def test_each_supported_provider_identifier_matches_exactly(self):
        aliases = {"call_uuid": "uuid", "recording_call_uuid": "call_uuid", "request_uuid": "request_id",
                   "a_leg_uuid": "ALegUUID", "b_leg_uuid": "BLegUUID"}
        for field, alias in aliases.items():
            doc = frappe._dict({field: " own-id ", "customer_number": self.doc.customer_number})
            correct = {alias: "own-id", "billsec": 7}
            wrong = {alias: "other-id", "destination_number": doc.customer_number, "billsec": 22}
            self.assertEqual(cdr.find_matching_cdr(doc, {"data": [wrong, correct]}), correct)

    def test_existing_id_never_falls_back_to_another_call_to_same_phone(self):
        self.doc.call_uuid = "own-id"
        response = {"data": [{"uuid": "other-id", "destination_number": self.doc.customer_number,
                              "recording_url": "https://media.vobiz.ai/other.mp3"}]}
        self.assertIsNone(cdr.find_matching_cdr(self.doc, response))

    def test_whitespace_provider_identifiers_are_treated_as_missing(self):
        self.doc.call_uuid = "  "
        self.assertEqual(cdr.sync_call_log_cdr("CALL")["status"], "Awaiting Provider ID")
        self.provider.search_cdrs.assert_not_called()

    def test_application_guard_rejects_unrelated_cdr_before_changing_any_fields(self):
        self.doc.call_uuid = "own-id"
        with self.assertRaisesRegex(ValueError, "does not match"):
            cdr.apply_cdr_to_call_log(self.doc, {"uuid": "other-id", "billsec": 22}, {})
        self.assertEqual(self.doc.cdr_json, "original")
        self.assertEqual(self.doc.billsec, 0)
        self.doc.save.assert_not_called()

    def test_missing_match_preserves_status_duration_and_recording(self):
        self.doc.call_uuid = "own-id"
        self.provider.search_cdrs.return_value = {"data": [{"uuid": "other-id", "billsec": 22}]}
        self.assertEqual(cdr.sync_call_log_cdr("CALL")["status"], "Not Found")
        self.assertEqual(self.doc.status, "Failed")
        self.assertEqual(self.doc.duration, 0)
        self.assertEqual(self.doc.recording_url, "")
        cdr.update_reference_call_metrics.assert_not_called()

    def test_valid_recording_and_cdr_still_sync_after_exact_identity_match(self):
        self.doc.call_uuid = "own-id"
        self.doc.status = "Connected"
        correct = {"uuid": "own-id", "status": "completed", "billsec": 22, "duration": 23,
                   "recording_url": "https://media.vobiz.ai/own.mp3", "cost": 0.01}
        self.provider.search_cdrs.return_value = {"data": [correct]}
        self.assertEqual(cdr.sync_call_log_cdr("CALL")["status"], "Synced")
        self.assertEqual(self.doc.status, "Completed")
        self.assertEqual(self.doc.billsec, 22)
        self.assertEqual(self.doc.recording_url, correct["recording_url"])
        self.assertEqual(self.doc.recording_status, "Completed")
        self.doc.save.assert_called_once()

    def test_agent_billing_does_not_overwrite_final_customer_failure(self):
        from vobiz_click_to_call.services.call_status import status_bucket
        for status, bucket in [("Failed", "failed"), ("No Answer", "no_answer"), ("Busy", "busy")]:
            with self.subTest(status=status):
                self.doc.update(call_uuid="own-id", status=status, call_status="customer-failed",
                                hangup_cause="CUSTOMER_FAILURE")
                cdr.apply_cdr_to_call_log(self.doc, {"uuid": "own-id", "billsec": 25,
                                                  "status": "completed", "hangup_cause": "NORMAL_CLEARING"}, {})
                self.assertEqual(self.doc.status, status)
                self.assertEqual(self.doc.hangup_cause, "CUSTOMER_FAILURE")
                self.assertEqual(status_bucket({"status": status, "billsec": 25}), bucket)

    def test_explicit_customer_outcome_wins_over_agent_billing(self):
        self.assertEqual(cdr.status_from_cdr({"status": "completed", "b_leg_status": "no-answer",
                                             "billsec": 25}, "Connected"), "No Answer")

    def test_active_parent_with_ended_customer_leg_is_not_terminal(self):
        self.assertFalse(cdr._terminal_cdr({"status": "in-progress", "dial_status": "completed",
                                          "end_time": "2026-09-14 16:45:00"}))

    def test_direct_exact_lookup_avoids_list_search(self):
        self.doc.call_uuid = "own-id"
        self.provider.retrieve_cdr.return_value = {"data": {"uuid": "own-id", "status": "completed"}}
        self.assertEqual(cdr.lookup_cdr(self.provider, self.doc)["uuid"], "own-id")
        self.provider.search_cdrs.assert_not_called()

    def test_uuid_changed_during_request_cannot_mark_new_call_not_found(self):
        self.doc.call_uuid = "old-id"
        latest = frappe._dict(self.doc, call_uuid="new-id")
        self.provider.search_cdrs.return_value = {"data": []}
        with patch.object(frappe, "get_doc", side_effect=[self.doc, latest]) as get_doc:
            self.assertEqual(cdr.sync_call_log_cdr("CALL")["status"], "Identity Changed")
        get_doc.assert_called_with("Vobiz Call Log", "CALL", for_update=True)
        self.db.set_value.assert_not_called()

    def test_callback_final_status_arriving_during_request_is_preserved(self):
        self.doc.update(call_uuid="own-id", status="Connected")
        latest = frappe._dict(self.doc, status="Busy", hangup_cause="USER_BUSY")
        self.provider.search_cdrs.return_value = {"data": [{"uuid": "own-id", "status": "completed", "billsec": 25}]}
        with patch.object(frappe, "get_doc", side_effect=[self.doc, latest]):
            self.assertEqual(cdr.sync_call_log_cdr("CALL")["status"], "Synced")
        self.assertEqual(latest.status, "Busy")
        self.assertEqual(latest.hangup_cause, "USER_BUSY")

    def test_manual_sync_cannot_finalize_a_still_active_provider_call(self):
        self.doc.update(call_uuid="own-id", status="Connected")
        self.provider.search_cdrs.return_value = {"data": [{"uuid": "own-id", "status": "in-progress",
                                                         "billsec": 25}]}
        self.assertEqual(cdr.sync_call_log_cdr("CALL")["status"], "Provider Active")
        self.assertEqual(self.doc.status, "Connected")
        self.doc.save.assert_not_called()

    def test_recent_sync_reserves_capacity_for_backlog_and_bounds_batch_size(self):
        from datetime import datetime
        from vobiz_click_to_call.services import recovery_policy
        recent = [frappe._dict(name=f"new-{i}") for i in range(4)]
        old = [frappe._dict(name=f"old-{i}") for i in range(8)]
        with patch.object(frappe, "get_all", side_effect=[recent, old]) as query, \
                patch.object(frappe, "enqueue") as enqueue, \
                patch.object(frappe.utils, "now_datetime", return_value=datetime(2026, 9, 16)), \
                patch.object(recovery_policy, "due", side_effect=lambda name, purpose: name != "new-0"):
            result = cdr.enqueue_recent_cdr_sync(limit=8, batch_size=500)
        self.assertEqual(result["queued"], 8)
        batches = [call.kwargs["call_logs"] for call in enqueue.call_args_list]
        self.assertEqual([len(batch) for batch in batches], [5, 3])
        selected = [name for batch in batches for name in batch]
        self.assertNotIn("new-0", selected)
        self.assertEqual(sum(name.startswith("old-") for name in selected), 5)
        self.assertIn("cdr_synced_at asc", query.call_args.kwargs["order_by"])
        self.assertEqual(query.call_args.kwargs["filters"]["status"], ["in", cdr.TERMINAL_STATUSES])


if __name__ == "__main__":
    unittest.main()
