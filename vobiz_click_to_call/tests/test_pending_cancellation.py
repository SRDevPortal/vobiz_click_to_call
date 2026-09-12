import json
import unittest
from unittest.mock import MagicMock, patch
import frappe
from vobiz_click_to_call.services import cancellation
from vobiz_click_to_call.services import client


class PendingCancellationTests(unittest.TestCase):
    def doc(self, **kwargs):
        return frappe._dict(dict(name="C1", status="Connected", call_uuid="uuid",
            a_leg_uuid="", b_leg_uuid="", response_json='{"cancel_requested":true}') | kwargs)

    def test_uuid_arrival_queues_after_commit(self):
        with patch.object(frappe, "enqueue") as enqueue:
            cancellation.queue_pending_cancel(self.doc())
            self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

    def test_no_uuid_and_finished_calls_are_not_sent(self):
        with patch.object(frappe, "enqueue") as enqueue:
            cancellation.queue_pending_cancel(self.doc(call_uuid=""))
            cancellation.queue_pending_cancel(self.doc(status="Completed"))
            enqueue.assert_not_called()

    def test_worker_commits_before_network_and_does_not_fake_completion(self):
        events = []
        db = MagicMock()
        db.commit.side_effect = lambda: events.append("commit")
        provider = MagicMock()
        provider.hangup_call.side_effect = lambda *a, **kw: events.append("hangup")
        with patch.object(frappe, "db", db), patch.object(frappe, "get_doc", return_value=self.doc()), patch.object(client, "VobizClient", return_value=provider):
            cancellation.cancel_pending("C1")
        self.assertEqual(events, ["commit", "hangup"])
        db.set_value.assert_not_called()

    def test_late_job_does_not_terminate_finished_call(self):
        with patch.object(frappe, "db", MagicMock()), patch.object(frappe, "get_doc", return_value=self.doc(status="Completed")), patch.object(client, "VobizClient") as provider:
            cancellation.cancel_pending("C1")
        provider.assert_not_called()

    def test_corrupt_or_absent_intent_is_not_a_cancellation(self):
        for value in ("bad", "[]", "{}", None):
            self.assertFalse(cancellation.cancellation_requested(self.doc(response_json=value)))
