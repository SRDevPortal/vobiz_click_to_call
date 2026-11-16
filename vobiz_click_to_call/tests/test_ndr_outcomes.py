import unittest
from datetime import datetime
from unittest.mock import Mock, patch
import frappe
from vobiz_click_to_call.api import ndr_outcomes as ndr

class NDROutcomeTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(ndr, "now_datetime", return_value=datetime(2026, 11, 13, 12))
        clock.start()
        self.addCleanup(clock.stop)
        self.hangup = patch.object(ndr, "hangup_time", return_value=datetime(2026, 11, 13, 12))
        self.hangup.start()
        self.addCleanup(self.hangup.stop)
        self.throw = patch.object(frappe, "throw", side_effect=ValueError)
        self.throw.start()
        self.addCleanup(self.throw.stop)
        self.translate = patch.object(ndr, "_", side_effect=lambda s: s)
        self.translate.start()
        self.addCleanup(self.translate.stop)

    def test_required_outcome_values(self):
        tags = ["Customer Unreachable", "Customer Requested Callback", "Escalation Required"]
        ndr.validate_values("Customer Unreachable", "", None, tags)
        for tag, notes, date in [("Unknown", "", None), ("Escalation Required", "", None)]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                ndr.validate_values(tag, notes, date, tags)

    def test_disabled_switch_preserves_existing_calls(self):
        with patch.object(frappe, "get_single", return_value={}):
            self.assertIsNone(ndr.validate_start("ENC-1", "Patient", "PAT-1"))

    def test_wrong_patient_is_rejected(self):
        encounter = Mock(patient="PAT-2")
        with patch.object(frappe, "get_single", return_value={"enable_ndr_outcomes": 1}), patch.object(ndr, "enabled_sources", return_value=["NDR"]), patch.object(frappe, "get_doc", return_value=encounter):
            with self.assertRaises(ValueError):
                ndr.validate_start("ENC-1", "Patient", "PAT-1")

    def test_duplicate_save_does_not_write(self):
        call = frappe._dict(name="CALL-1", status="Completed")
        encounter = frappe._dict(name="ENC-1")
        db = Mock()
        db.get_value.return_value = "LOG-1"
        with patch.object(ndr, "authorized_call", return_value=(call, encounter)), patch.object(frappe, "db", db), patch.object(frappe, "get_doc") as get_doc:
            self.assertEqual(ndr.save.__wrapped__("CALL-1", "Customer Unreachable"), {"name": "LOG-1", "duplicate": True})
            db.set_value.assert_not_called()
            get_doc.assert_not_called()

    def test_active_call_cannot_save(self):
        with patch.object(ndr, "authorized_call", return_value=(frappe._dict(status="Connected"), None)):
            with self.assertRaises(ValueError):
                ndr.save.__wrapped__("CALL-1", "Customer Unreachable")

    def test_save_only_writes_ndr_fields(self):
        call = frappe._dict(name="CALL-1", status="Completed", creation="2026-11-13 10:00:00")
        encounter = Mock(name="unused")
        encounter.name="ENC-1"
        encounter.get.return_value=None
        db=Mock()
        db.get_value.return_value=None
        log=Mock()
        log.insert.return_value=frappe._dict(name="LOG-1")
        with patch.object(ndr, "authorized_call", return_value=(call,encounter)), patch.object(frappe, "db",db), patch.object(ndr,"tags",return_value=["Customer Unreachable"]), patch.object(frappe,"get_doc",return_value=log), patch.object(frappe,"session",frappe._dict(user="agent@example.com")):
            ndr.save.__wrapped__("CALL-1","Customer Unreachable")
        writes=db.set_value.call_args_list
        self.assertEqual(len(writes),2)
        self.assertEqual(writes[0].args[0],"Patient Encounter")
        self.assertTrue(all(key.startswith("ndr_") for key in writes[0].args[2]))
        self.assertEqual(writes[1].args[2],{"ndr_outcome_state":"Saved"})

    def test_other_agent_cannot_save(self):
        call=frappe._dict(user="another@example.com",ndr_encounter="ENC-1")
        with patch.object(ndr,"enabled_sources",return_value=["NDR"]), patch.object(frappe,"session",frappe._dict(user="agent@example.com")), patch.object(frappe,"get_doc",return_value=call), patch.object(frappe,"get_roles",return_value=[]):
            with self.assertRaises(ValueError):
                ndr.authorized_call("CALL-1")

    def test_late_old_call_does_not_replace_newer_encounter_summary(self):
        call = frappe._dict(name="OLD-CALL", status="Completed", creation="2026-11-12 10:00:00")
        encounter = Mock()
        encounter.name = "ENC-1"
        encounter.get.return_value = "NEW-CALL"
        db = Mock()
        db.get_value.side_effect = [None, "2026-11-13 10:00:00"]
        log = Mock()
        log.insert.return_value = frappe._dict(name="LOG-OLD")
        with patch.object(ndr, "authorized_call", return_value=(call, encounter)), patch.object(frappe, "db", db), patch.object(ndr, "tags", return_value=["Customer Unreachable"]), patch.object(frappe, "get_doc", return_value=log), patch.object(frappe, "session", frappe._dict(user="agent@example.com")):
            ndr.save.__wrapped__("OLD-CALL", "Customer Unreachable")
        db.set_value.assert_called_once_with("Vobiz Call Log", "OLD-CALL", {"ndr_outcome_state": "Saved"})


    def test_callback_no_longer_requires_future_timestamp(self):
        ndr.validate_values("Customer Requested Callback", "", "2000-01-01", ["Customer Requested Callback"])

    def test_hangup_timestamp_uses_call_end_and_is_stable(self):
        self.hangup.stop()
        call = frappe._dict(name="CALL-1", status="Completed", end_time="2026-11-13 10:15:00", modified="2026-11-13 11:00:00")
        with patch.object(frappe, "db", Mock()) as db:
            first = ndr.hangup_time(call)
            self.assertEqual(first, datetime(2026, 11, 13, 10, 15))
            self.assertEqual(ndr.hangup_time(call), first)
            self.assertEqual(db.set_value.call_count, 1)

    def test_local_completion_uses_recorded_reset_time(self):
        self.hangup.stop()
        call = frappe._dict(name="CALL-1", status="Cancelled", request_json='{"manual_local_reset":{"at":"2026-11-13 10:20:00"}}')
        with patch.object(frappe, "db", Mock()):
            self.assertEqual(ndr.hangup_time(call), datetime(2026, 11, 13, 10, 20))

    def test_short_tags_retain_note_requirements(self):
        for tag in ("Reattempt", "Address Update", "Refused", "Escalate"):
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError):
                    ndr.validate_values(tag, "", None, [tag])
                ndr.validate_values(tag, "Customer instructions", None, [tag])
        ndr.validate_values("Callback", "", None, ["Callback"])

    def test_callback_schedule_rejects_past_time(self):
        call = frappe._dict(name="CALL-1", status="Completed")
        encounter = frappe._dict(name="ENC-1")
        db = Mock()
        db.get_value.return_value = None
        with patch.object(ndr, "authorized_call", return_value=(call, encounter)), patch.object(frappe, "db", db), patch.object(ndr, "tags", return_value=["Callback"]), patch.object(frappe, "get_doc") as get_doc:
            with self.assertRaises(ValueError):
                ndr.save.__wrapped__("CALL-1", "Callback", callback_at="2000-01-01")
            get_doc.assert_not_called()

    def test_blank_callback_is_sql_null_for_reattempt(self):
        for value in (None, "", "   "):
            with self.subTest(callback_at=value):
                call = frappe._dict(name="CALL-1", status="Completed", creation="2026-11-13 10:00:00")
                encounter = Mock()
                encounter.name = "ENC-1"
                encounter.get.return_value = None
                db = Mock()
                db.get_value.return_value = None
                log = Mock()
                log.insert.return_value = frappe._dict(name="LOG-1")
                with patch.object(ndr, "authorized_call", return_value=(call, encounter)), patch.object(frappe, "db", db), patch.object(ndr, "tags", return_value=["Reattempt"]), patch.object(frappe, "get_doc", return_value=log) as get_doc, patch.object(frappe, "session", frappe._dict(user="agent@example.com")):
                    ndr.save.__wrapped__("CALL-1", "Reattempt", notes="reattempt order", callback_at=value)
                self.assertIsNone(get_doc.call_args.args[0]["callback_at"])
                self.assertIsNone(db.set_value.call_args_list[0].args[2]["ndr_callback_at"])
