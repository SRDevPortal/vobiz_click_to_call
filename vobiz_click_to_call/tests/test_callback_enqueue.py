from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vobiz_click_to_call.api import webhook


class TestCallbackEnqueue(unittest.TestCase):
    def test_raw_callback_arguments_are_enqueued_directly(self):
        payload = {"CallUUID": "call-1", "Event": "Ring"}

        with (
            patch.object(webhook, "get_settings", return_value=SimpleNamespace(store_raw_payloads=1)),
            patch.object(webhook.frappe, "enqueue") as enqueue,
        ):
            webhook._append_callback_if_enabled("CTC-1", "ring", payload)

        enqueue.assert_called_once_with(
            "vobiz_click_to_call.services.callback_logging.append_callback_job",
            queue="short",
            timeout=120,
            enqueue_after_commit=True,
            call_log="CTC-1",
            event_type="ring",
            payload=payload,
        )

    def test_worker_adapter_survives_enqueue_reserved_parameters(self):
        import inspect
        from frappe.utils.background_jobs import enqueue
        from vobiz_click_to_call.services.callback_logging import append_callback_job
        bound = inspect.signature(enqueue).bind("method", event_type="ring", call_log="CALL", payload={})
        worker_args = bound.arguments["kwargs"]
        with patch("vobiz_click_to_call.services.callback_logging.append_callback") as append:
            append_callback_job(**worker_args)
        append.assert_called_once_with(call_log="CALL", event="ring", payload={})

    def test_mapping_defaults_do_not_choose_arbitrary_records(self):
        from vobiz_click_to_call.api.inbound import unknown_inbound_lead_defaults
        self.assertEqual(unknown_inbound_lead_defaults({})["pipeline"], "")
        result = unknown_inbound_lead_defaults({"default_pipeline": "Selected", "default_platform": "Website", "default_source": "Google"})
        self.assertEqual(result["pipeline"], "Selected")
        self.assertEqual(result["platform"], "Website")
        self.assertEqual(result["source"], "Google")


if __name__ == "__main__":
    unittest.main()
