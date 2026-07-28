from __future__ import annotations

from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1]
WEBHOOK = APP / "api" / "webhook.py"
INBOUND = APP / "api" / "inbound.py"
CONSOLE = APP / "api" / "console.py"
CALL = APP / "api" / "call.py"
RECORDING = APP / "api" / "recording.py"
AI = APP / "services" / "ai.py"
CDR = APP / "services" / "cdr.py"
DEBUG_LOG = APP / "services" / "debug_log.py"
PATCHES = APP / "patches.txt"
INDEX_PATCH = APP / "patches" / "v1_0" / "add_call_log_performance_indexes.py"
CLICK_TO_CALL = APP / "public" / "js" / "click_to_call.js"
AVAILABILITY = APP / "public" / "js" / "availability.js"
AGENT_CONSOLE = (
    APP
    / "vobiz_click_to_call"
    / "page"
    / "vobiz_agent_console"
    / "vobiz_agent_console.js"
)


class TestServerSafetyGuards(unittest.TestCase):
    def test_guest_callbacks_are_rate_limited_and_fail_closed(self):
        webhook = WEBHOOK.read_text(encoding="utf-8")
        inbound = INBOUND.read_text(encoding="utf-8")

        self.assertEqual(webhook.count("@frappe.whitelist(allow_guest=True,"), 9)
        self.assertEqual(webhook.count("@rate_limit(limit=300, seconds=60)"), 9)
        self.assertEqual(inbound.count("@frappe.whitelist(allow_guest=True,"), 3)
        self.assertEqual(inbound.count("@rate_limit(limit=300, seconds=60)"), 3)
        self.assertIn("if not expected:\n        return False", webhook)
        self.assertIn("if not expected_token:\n        return False", inbound)
        self.assertIn("if not settings.enabled:", inbound)

    def test_payloads_and_recordings_have_hard_size_limits(self):
        webhook = WEBHOOK.read_text(encoding="utf-8")
        debug_log = DEBUG_LOG.read_text(encoding="utf-8")
        recording = RECORDING.read_text(encoding="utf-8")

        self.assertIn("def _bounded_payload(payload: dict, max_chars: int = 64 * 1024)", webhook)
        self.assertIn("MAX_DIAGNOSTIC_PAYLOAD_CHARS = 20 * 1024", debug_log)
        self.assertIn("MAX_RECORDING_BYTES = 100 * 1024 * 1024", recording)
        self.assertIn("stream=True", recording)
        self.assertNotIn("response.content", recording)

    def test_polling_does_not_hit_provider_or_overlap(self):
        call_api = CALL.read_text(encoding="utf-8")
        click_to_call = CLICK_TO_CALL.read_text(encoding="utf-8")
        agent_console = AGENT_CONSOLE.read_text(encoding="utf-8")
        availability = AVAILABILITY.read_text(encoding="utf-8")

        self.assertIn("sync_provider: int | str = 1", call_api)
        self.assertIn("if frappe.utils.cint(sync_provider):", call_api)
        self.assertIn("sync_provider: 0", click_to_call)
        self.assertIn("statusPollInFlight", click_to_call)
        self.assertIn("sync_provider: 0", agent_console)
        self.assertIn("this.load_in_flight", agent_console)
        self.assertIn('route[0] !== "vobiz-agent-console"', availability)

    def test_cdr_jobs_are_bounded_and_deduplicated(self):
        cdr = CDR.read_text(encoding="utf-8")
        ai = AI.read_text(encoding="utf-8")

        self.assertIn("deduplicate=True", cdr)
        self.assertIn('queue="long"', cdr)
        self.assertIn("min(int(limit or 100), 100)", cdr)
        self.assertIn("def _bounded_json(value: Any, max_chars: int = 64 * 1024)", cdr)
        self.assertNotIn("        classify_call_log(call_log)", ai)

    def test_call_log_indexes_are_declared_in_a_migration_patch(self):
        patches = PATCHES.read_text(encoding="utf-8")
        index_patch = INDEX_PATCH.read_text(encoding="utf-8")

        self.assertIn(
            "vobiz_click_to_call.patches.v1_0.add_call_log_performance_indexes",
            patches,
        )
        for index_name in (
            "idx_vobiz_call_cdr_status_creation",
            "idx_vobiz_call_reference_creation",
            "idx_vobiz_call_reference_activity",
            "idx_vobiz_call_customer_creation",
            "idx_vobiz_call_uuid",
            "idx_vobiz_call_recording_id",
        ):
            self.assertIn(index_name, index_patch)
        self.assertIn("algorithm=inplace, lock=none", index_patch)

    def test_analytics_queries_are_bounded(self):
        console = CONSOLE.read_text(encoding="utf-8")

        self.assertIn("ANALYTICS_MAX_DAYS = 31", console)
        self.assertIn("ANALYTICS_CACHE_SECONDS = 30", console)
        self.assertIn("calls_only: int | str = 0", console)
        self.assertNotIn("limit_page_length=50000", console)
        self.assertNotIn("limit_page_length=10000", console)
        self.assertNotIn("_visible_crm_lead_names", console)


if __name__ == "__main__":
    unittest.main()
