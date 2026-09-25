import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
from requests.exceptions import ReadTimeout
from vobiz_click_to_call.api import call
from vobiz_click_to_call.services import confirmation, mapping_recovery, realtime
from vobiz_click_to_call.services import cancellation


class ProviderConfirmationTests(unittest.TestCase):
    def doc(self, **values):
        return frappe._dict(dict(name="C1", user="agent@test.invalid", status="Queued",
            call_status=None, call_uuid=None, a_leg_uuid=None, b_leg_uuid=None,
            error_message=None, end_time=None, response_json="{}", reload=MagicMock()) | values)

    def test_timeout_persists_valid_nonterminal_status_and_does_not_redial(self):
        doc = self.doc(call_status=confirmation.PENDING)
        db = MagicMock()
        with patch.object(frappe, "db", db), patch.object(frappe, "session", frappe._dict(user=doc.user)), \
                patch.object(call, "log_vobiz_event"), patch.object(frappe, "get_traceback", return_value="timeout"):
            self.assertTrue(call._mark_confirmation_pending(doc, ReadTimeout("timeout")))
        sql = db.sql.call_args.args[0]
        self.assertIn("`status` = 'Queued'", sql)
        self.assertIn(confirmation.PENDING, sql)
        self.assertIn("AND `status` IN ('Queued', 'Initiated')", sql)
        self.assertNotIn("Provider Unconfirmed", sql)
        self.assertNotIn("Queued", mapping_recovery.TERMINAL_STATUSES)
        self.assertNotIn("Provider Unconfirmed", mapping_recovery.TERMINAL_STATUSES)

    def test_callback_winning_timeout_race_is_preserved(self):
        doc = self.doc(status="Connected", call_uuid="uuid", call_status="answered")
        with patch.object(frappe, "db", MagicMock()), patch.object(frappe, "session", frappe._dict(user=doc.user)), \
                patch.object(call, "log_vobiz_event") as log:
            self.assertFalse(call._mark_confirmation_pending(doc, ReadTimeout("timeout")))
        log.assert_not_called()
        self.assertEqual(doc.status, "Connected")

    def test_both_legacy_statuses_normalize_without_fabricated_termination(self):
        for old in confirmation.LEGACY_STATUSES:
            doc = self.doc(status=old, error_message="original timeout")
            confirmation.normalize_legacy_status(doc)
            self.assertEqual(doc.status, "Queued")
            self.assertEqual(doc.call_status, confirmation.PENDING)
            self.assertIsNone(doc.end_time)
            first = dict(doc)
            confirmation.normalize_legacy_status(doc)
            self.assertEqual(first, doc)

    def test_existing_cancellation_intent_survives_normalization(self):
        doc = self.doc(status="Provider Unconfirmed", call_status="cancellation-requested")
        confirmation.normalize_legacy_status(doc)
        self.assertEqual(doc.call_status, "cancellation-requested")

    def test_valid_statuses_are_not_changed(self):
        for status in ["Initiated", "Queued", "Ringing", "Connected", "Completed", "Failed", "Cancelled"]:
            doc = self.doc(status=status); before = dict(doc)
            confirmation.normalize_legacy_status(doc)
            self.assertEqual(before, doc)

    def test_legacy_repair_rechecks_locked_row_and_preserves_concurrent_completion(self):
        old = self.doc(status="Provider Unconfirmed")
        latest = self.doc(status="Completed", save=MagicMock())
        with patch.object(frappe, "db", MagicMock()), patch.object(frappe, "get_doc", return_value=latest):
            confirmation.normalize_existing(old)
        latest.save.assert_not_called()
        old.reload.assert_called_once()

    def test_provider_not_found_does_not_fabricate_end_or_release(self):
        doc = self.doc(status="Connected", call_uuid="uuid")
        client = MagicMock(); client.retrieve_live_call.side_effect = RuntimeError("Call not found")
        with patch.object(call, "VobizClient", return_value=client), patch.object(call, "get_settings"), \
                patch.object(call, "save_doc_latest") as save, \
                patch.object(call, "restore_mapping_after_call") as release, \
                patch.object(mapping_recovery, "enqueue_recovery") as recover:
            call.sync_live_call_if_finished(doc)
        save.assert_not_called(); release.assert_not_called(); recover.assert_called_once_with("C1")
        self.assertEqual(doc.status, "Connected")
        self.assertIsNone(doc.end_time)

    def test_completion_contract_contains_reference_and_direction(self):
        doc = self.doc(status="Completed", direction="Outgoing", reference_doctype="CRM Lead", reference_name="L1")
        with patch.object(frappe, "publish_realtime") as publish:
            realtime.publish_call_disconnected(doc)
        payload = publish.call_args.args[1]
        self.assertEqual(payload["reference_name"], "L1")
        self.assertEqual(payload["direction"], "Outgoing")
        self.assertTrue(publish.call_args.kwargs["after_commit"])

    def test_pending_confirmation_never_publishes_disconnected(self):
        with patch.object(frappe, "publish_realtime") as publish:
            realtime.publish_call_disconnected(self.doc(call_status=confirmation.PENDING))
        publish.assert_not_called()

    def test_wrong_provider_uuid_cannot_complete_call(self):
        doc = self.doc(status="Connected", call_uuid="uuid")
        client = MagicMock(); client.retrieve_live_call.return_value = {"call_uuid": "other", "status": "completed"}
        with patch.object(call, "VobizClient", return_value=client), patch.object(call, "get_settings"), \
                patch.object(call, "save_doc_latest") as save:
            call.sync_live_call_if_finished(doc)
        save.assert_not_called()

    def test_end_call_retains_reservation_until_provider_confirmation(self):
        for provider_error in (None, ReadTimeout("timeout")):
            doc = self.doc(status="Connected", call_uuid="uuid")
            db = MagicMock()
            events = []
            db.commit.side_effect = lambda: events.append("commit")
            def end(_):
                self.assertIn("commit", events)
                self.assertTrue(json.loads(doc.response_json)["cancel_requested"])
                if provider_error:
                    raise provider_error
            with patch.object(frappe, "db", db), patch.object(frappe, "session", frappe._dict(user=doc.user)), \
                    patch.object(frappe.local, "flags", frappe._dict(in_test=False), create=True), \
                    patch.object(frappe, "get_doc", return_value=doc), patch.object(frappe, "get_roles", return_value=[]), \
                    patch.object(call, "log_vobiz_event"), patch.object(call, "snapshot_doc", return_value={}), \
                    patch.object(call, "save_doc_latest", side_effect=lambda d, before: d), \
                    patch.object(cancellation, "cancel_pending", side_effect=end), \
                    patch.object(mapping_recovery, "enqueue_recovery") as recover, \
                    patch.object(call, "restore_mapping_after_call") as release:
                if provider_error:
                    with self.assertRaises(ReadTimeout):
                        call.cancel_call("C1")
                else:
                    result = call.cancel_call("C1")
                    self.assertTrue(result["pending_provider"])
            recover.assert_called_once_with("C1")
            release.assert_not_called()
            self.assertEqual(doc.status, "Connected")
            self.assertIsNone(doc.end_time)
