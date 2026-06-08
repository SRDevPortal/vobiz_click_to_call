from __future__ import annotations

import unittest

from vobiz_click_to_call.api.webhook import _status_from_dial_status, _status_from_hangup


class TestWebhookStatusMapping(unittest.TestCase):
    def test_pre_bridge_hangup_is_terminal_cancelled(self):
        for previous in ("Queued", "Ringing", "Customer Answered", "Agent Answered", "Agent Ringing"):
            with self.subTest(previous=previous):
                self.assertEqual(_status_from_hangup("completed", "", previous=previous), "Cancelled")
                self.assertEqual(_status_from_hangup("hangup", "", previous=previous), "Cancelled")

    def test_connected_hangup_completes_call(self):
        self.assertEqual(_status_from_hangup("completed", "", previous="Connected"), "Completed")
        self.assertEqual(_status_from_hangup("hangup", "", previous="Connected"), "Completed")

    def test_specific_failure_signals_win_over_generic_completed(self):
        self.assertEqual(_status_from_hangup("completed", "busy", previous="Agent Ringing"), "Busy")
        self.assertEqual(_status_from_hangup("completed", "no-answer", previous="Customer Answered"), "No Answer")
        self.assertEqual(_status_from_hangup("completed", "failed", previous="Ringing"), "Failed")

    def test_dial_hangup_before_connect_stays_non_completed_until_hangup_webhook(self):
        self.assertEqual(_status_from_dial_status("hangup", previous="Customer Answered"), "Customer Answered")
        self.assertEqual(_status_from_dial_status("hangup", previous="Connected"), "Completed")


if __name__ == "__main__":
    unittest.main()
