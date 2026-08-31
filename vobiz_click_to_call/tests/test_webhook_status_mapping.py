from __future__ import annotations

import unittest
from unittest.mock import patch

from vobiz_click_to_call.api.webhook import _status_from_dial_status, _status_from_hangup
from vobiz_click_to_call.api.console import _analytics_bucket
from vobiz_click_to_call.services.call_log_update import save_doc_latest
from vobiz_click_to_call.services import call_log_update
from vobiz_click_to_call.services.call_status import (
    is_inbound_missed_call,
    status_bucket,
    status_from_provider,
    talk_seconds,
)
from vobiz_click_to_call.services.disposition import call_next_action_label


class TestWebhookStatusMapping(unittest.TestCase):
    def test_timestamp_retry_reapplies_only_changed_fields(self):
        class Field:
            def __init__(self, fieldname):
                self.fieldname = fieldname
                self.fieldtype = "Data"

        class Meta:
            fields = [Field("status"), Field("recording_url")]

        class FakeDoc:
            doctype = "Vobiz Call Log"
            name = "CALL-1"
            meta = Meta()

            def __init__(self, status, recording_url="", fail_once=False):
                self.status = status
                self.recording_url = recording_url
                self.fail_once = fail_once
                self.saved = False

            def get(self, fieldname):
                return getattr(self, fieldname)

            def set(self, fieldname, value):
                setattr(self, fieldname, value)

            def save(self, ignore_permissions=True):
                if self.fail_once:
                    self.fail_once = False
                    raise call_log_update.frappe.TimestampMismatchError()
                self.saved = True

        stale = FakeDoc("Queued", fail_once=True)
        before = {"status": "Queued", "recording_url": ""}
        stale.status = "Completed"
        latest = FakeDoc("Connected", recording_url="https://recording.example/file.mp3")

        with patch.object(call_log_update.frappe, "get_doc", return_value=latest):
            saved = save_doc_latest(stale, before)

        self.assertIs(saved, latest)
        self.assertTrue(latest.saved)
        self.assertEqual(latest.status, "Completed")
        self.assertEqual(latest.recording_url, "https://recording.example/file.mp3")

    def test_pre_bridge_hangup_is_terminal_cancelled(self):
        for previous in ("Queued", "Ringing", "Customer Answered", "Agent Answered", "Agent Ringing"):
            with self.subTest(previous=previous):
                self.assertEqual(_status_from_hangup("completed", "", previous=previous), "Cancelled")
                self.assertEqual(_status_from_hangup("hangup", "", previous=previous), "Cancelled")

    def test_connected_hangup_without_talk_time_is_no_answer(self):
        self.assertEqual(_status_from_hangup("completed", "", previous="Connected"), "No Answer")
        self.assertEqual(_status_from_hangup("hangup", "", previous="Connected"), "No Answer")
        self.assertEqual(_status_from_hangup("completed", "", previous="Connected", billsec=12), "Completed")

    def test_billable_normal_clearing_completes_call_even_without_prior_connected(self):
        self.assertEqual(
            _status_from_hangup("completed", "NORMAL_CLEARING", previous="Customer Answered", billsec=60),
            "Completed",
        )
        self.assertEqual(
            _analytics_bucket(
                {
                    "status": "Cancelled",
                    "call_status": "completed",
                    "hangup_cause": "NORMAL_CLEARING",
                    "billsec": 60,
                    "duration": 1,
                }
            ),
            "connected",
        )

    def test_completed_without_talk_time_is_not_analytics_connected(self):
        self.assertEqual(
            _analytics_bucket(
                {
                    "status": "Completed",
                    "call_status": "completed",
                    "billsec": 0,
                    "recording_duration": 0,
                    "duration": 90,
                }
            ),
            "no_answer",
        )

    def test_completed_with_talk_time_is_analytics_connected(self):
        self.assertEqual(_analytics_bucket({"status": "Completed", "billsec": 12}), "connected")

    def test_recording_alone_does_not_make_call_connected(self):
        self.assertEqual(_analytics_bucket({"status": "Completed", "recording_duration": 12}), "no_answer")

    def test_missed_status_overrides_short_recording(self):
        for status, expected_bucket in (("No Answer", "no_answer"), ("Busy", "busy")):
            with self.subTest(status=status):
                self.assertEqual(
                    _analytics_bucket(
                        {
                            "direction": "Incoming",
                            "call_flow": "Customer First",
                            "status": status,
                            "call_status": "ringing",
                            "billsec": 0,
                            "duration": 0,
                            "recording_duration": 12,
                            "answer_time": None,
                        }
                    ),
                    expected_bucket,
                )

    def test_talk_time_starts_from_customer_answer_for_customer_first_calls(self):
        self.assertEqual(
            talk_seconds(
                {
                    "call_flow": "Customer First",
                    "answer_time": "2026-07-13 10:00:00",
                    "end_time": "2026-07-13 10:00:45",
                    "billsec": 20,
                    "recording_duration": 0,
                }
            ),
            45,
        )

    def test_customer_answer_duration_does_not_make_call_connected(self):
        row = {
            "call_flow": "Customer First",
            "status": "No Answer",
            "answer_time": "2026-07-13 10:00:00",
            "end_time": "2026-07-13 10:00:45",
            "billsec": 0,
            "recording_duration": 0,
        }

        self.assertEqual(talk_seconds(row), 45)
        self.assertEqual(status_bucket(row), "no_answer")

    def test_agent_first_talk_time_prefers_customer_leg_duration_over_billsec(self):
        self.assertEqual(
            talk_seconds(
                {
                    "call_flow": "Agent First",
                    "duration": 124,
                    "billsec": 180,
                    "recording_duration": 0,
                }
            ),
            124,
        )

    def test_talk_time_has_no_billsec_or_recording_fallback(self):
        self.assertEqual(talk_seconds({"call_flow": "Customer First", "billsec": 60, "recording_duration": 30}), 0)
        self.assertEqual(talk_seconds({"call_flow": "Agent First", "billsec": 60, "recording_duration": 30}), 0)

    def test_specific_failure_signals_win_over_generic_completed(self):
        self.assertEqual(_status_from_hangup("completed", "busy", previous="Agent Ringing"), "Busy")
        self.assertEqual(_status_from_hangup("completed", "no-answer", previous="Customer Answered"), "No Answer")
        self.assertEqual(_status_from_hangup("completed", "failed", previous="Ringing"), "Failed")

    def test_agent_first_b_leg_outcome_wins_over_a_leg_billsec(self):
        for status, expected_status, expected_bucket in (
            ("busy", "Busy", "busy"),
            ("no-answer", "No Answer", "no_answer"),
        ):
            with self.subTest(status=status):
                row = {
                    "status": expected_status,
                    "dial_status": status,
                    "call_status": "completed",
                    "call_flow": "Agent First",
                    "billsec": 60,
                    "duration": 0,
                }
                self.assertEqual(status_from_provider(row), expected_status)
                self.assertEqual(status_bucket(row), expected_bucket)

    def test_locally_cancelled_agent_first_call_with_customer_talk_is_connected(self):
        row = {
            "status": "Cancelled",
            "dial_status": "hangup",
            "error_message": "Call cancelled by user.",
            "call_flow": "Agent First",
            "duration": 1377,
            "billsec": 1380,
            "recording_duration": 1376,
        }

        self.assertEqual(status_bucket(row), "connected")

    def test_explicit_customer_rejection_still_wins_over_talk_fields(self):
        row = {
            "status": "Cancelled",
            "dial_status": "reject",
            "call_flow": "Agent First",
            "duration": 1377,
            "billsec": 1380,
            "recording_duration": 1376,
        }

        self.assertEqual(status_bucket(row), "cancelled")

    def test_generic_hangup_preserves_b_leg_failure_despite_a_leg_billsec(self):
        self.assertEqual(
            _status_from_hangup("completed", "NORMAL_CLEARING", previous="Busy", billsec=60),
            "Busy",
        )
        self.assertEqual(
            _status_from_hangup("completed", "NORMAL_CLEARING", previous="No Answer", billsec=60),
            "No Answer",
        )

    def test_dial_hangup_before_connect_is_terminal_cancelled(self):
        self.assertEqual(_status_from_dial_status("hangup", previous="Customer Answered"), "Cancelled")
        self.assertEqual(_status_from_dial_status("hangup", previous="Agent Answered"), "Cancelled")
        self.assertEqual(_status_from_dial_status("hangup", previous="Connected"), "Completed")

    def test_cancelled_call_next_action_labels_party(self):
        self.assertEqual(
            call_next_action_label(
                {
                    "status": "Cancelled",
                    "call_flow": "Customer First",
                    "error_message": "Call cancelled by user.",
                }
            ),
            "Cancelled by Agent",
        )
        self.assertEqual(
            call_next_action_label(
                {
                    "status": "Cancelled",
                    "call_flow": "Customer First",
                }
            ),
            "Cancelled by Customer",
        )
        self.assertEqual(
            call_next_action_label(
                {
                    "status": "Cancelled",
                    "call_flow": "Customer First",
                    "answer_time": "2026-07-01 12:00:00",
                }
            ),
            "Cancelled by Agent",
        )

    def test_missed_call_definition_is_inbound_customer_call_only(self):
        self.assertFalse(
            is_inbound_missed_call(
                {
                    "direction": "Outgoing",
                    "status": "No Answer",
                    "call_flow": "Customer First",
                }
            )
        )
        self.assertFalse(
            is_inbound_missed_call(
                {
                    "direction": "Outgoing",
                    "status": "Busy",
                    "call_flow": "Customer First",
                }
            )
        )
        self.assertFalse(
            is_inbound_missed_call(
                {
                    "direction": "Outgoing",
                    "status": "Cancelled",
                    "call_flow": "Customer First",
                }
            )
        )
        self.assertTrue(
            is_inbound_missed_call(
                {
                    "direction": "Incoming",
                    "status": "Busy",
                    "dial_status": "busy",
                    "call_flow": "Customer First",
                }
            )
        )
        self.assertTrue(
            is_inbound_missed_call(
                {
                    "direction": "Incoming",
                    "status": "No Answer",
                    "call_status": "missed",
                    "call_flow": "Customer First",
                }
            )
        )
        self.assertFalse(
            is_inbound_missed_call(
                {
                    "direction": "Incoming",
                    "status": "No Answer",
                    "billsec": 45,
                    "call_flow": "Customer First",
                }
            )
        )

    def test_static_transcription_event_endpoint_matches_provider_payload(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "api" / "webhook.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def transcription_event():", source)
        self.assertIn("_find_call_log_from_provider_payload(data)", source)
        self.assertIn('"call_uuid"', source)
        self.assertIn("_apply_transcription_payload(doc, data, payload)", source)


if __name__ == "__main__":
    unittest.main()
