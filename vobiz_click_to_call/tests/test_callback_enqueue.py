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
            "vobiz_ai.api.call_log.append_callback",
            queue="short",
            timeout=120,
            enqueue_after_commit=True,
            call_log="CTC-1",
            event="ring",
            payload=payload,
        )


if __name__ == "__main__":
    unittest.main()
