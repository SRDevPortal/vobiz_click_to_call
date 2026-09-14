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
            (frappe, "get_roles", lambda: []), (frappe, "get_doc", lambda *args: self.doc),
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


if __name__ == "__main__":
    unittest.main()
