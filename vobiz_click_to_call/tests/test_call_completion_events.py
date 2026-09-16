import unittest
from unittest.mock import patch

import frappe

from vobiz_click_to_call.services.realtime import publish_call_disconnected


class CallCompletionEventTests(unittest.TestCase):
    def test_completion_includes_disposition_context_after_commit_for_own_agent(self):
        for direction in ("Incoming", "Outgoing"):
            with self.subTest(direction=direction):
                doc = frappe._dict(
                    name="CALL-1", status="Completed", user="agent@example.test",
                    direction=direction, reference_doctype="CRM Lead", reference_name="LEAD-1",
                    customer_number="1234567890", answer_time="2026-09-16 10:00:00",
                    end_time="2026-09-16 10:01:00", call_flow="Customer First",
                )
                with patch.object(frappe, "publish_realtime") as publish:
                    publish_call_disconnected(doc)
                publish.assert_called_once()
                event, payload = publish.call_args.args
                self.assertEqual(event, "vobiz_call_disconnected")
                for field in ("name", "status", "direction", "reference_doctype", "reference_name",
                              "answer_time", "end_time", "call_flow"):
                    self.assertEqual(payload[field], doc[field])
                self.assertEqual(publish.call_args.kwargs, {"user": doc.user, "after_commit": True})

    def test_unfinished_or_unassigned_calls_do_not_publish_completion(self):
        for status, user in [("Queued", "agent@example.test"), ("Connected", "agent@example.test"),
                             ("Completed", "")]:
            with self.subTest(status=status, user=user), patch.object(frappe, "publish_realtime") as publish:
                publish_call_disconnected(frappe._dict(status=status, user=user))
                publish.assert_not_called()
