from __future__ import annotations

import unittest

from vobiz_click_to_call.api.webhook import _status_from_dial_status, _status_from_hangup
from vobiz_click_to_call.api.console import _analytics_bucket
from vobiz_click_to_call.services.disposition import call_next_action_label


class TestWebhookStatusMapping(unittest.TestCase):
    def test_pre_bridge_hangup_is_terminal_cancelled(self):
        for previous in ("Queued", "Ringing", "Customer Answered", "Agent Answered", "Agent Ringing"):
            with self.subTest(previous=previous):
                self.assertEqual(_status_from_hangup("completed", "", previous=previous), "Cancelled")
                self.assertEqual(_status_from_hangup("hangup", "", previous=previous), "Cancelled")

    def test_connected_hangup_completes_call(self):
        self.assertEqual(_status_from_hangup("completed", "", previous="Connected"), "Completed")
        self.assertEqual(_status_from_hangup("hangup", "", previous="Connected"), "Completed")

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

    def test_specific_failure_signals_win_over_generic_completed(self):
        self.assertEqual(_status_from_hangup("completed", "busy", previous="Agent Ringing"), "Busy")
        self.assertEqual(_status_from_hangup("completed", "no-answer", previous="Customer Answered"), "No Answer")
        self.assertEqual(_status_from_hangup("completed", "failed", previous="Ringing"), "Failed")

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
