from __future__ import annotations

from pathlib import Path
import unittest

from vobiz_click_to_call import hooks as app_hooks


APP = Path(__file__).resolve().parents[1]
WEBHOOK = APP / "api" / "webhook.py"
INBOUND = APP / "api" / "inbound.py"
CONSOLE = APP / "api" / "console.py"
CALL = APP / "api" / "call.py"
RECORDING = APP / "api" / "recording.py"
RECORDING_SERVICE = APP / "services" / "recording.py"
DELETE_CLEANUP = APP / "services" / "delete_cleanup.py"
AI = APP / "services" / "ai.py"
CDR = APP / "services" / "cdr.py"
DEBUG_LOG = APP / "services" / "debug_log.py"
PATCHES = APP / "patches.txt"
INDEX_PATCH = APP / "patches" / "v1_0" / "add_call_log_performance_indexes.py"
ATTENDANCE_INDEX_PATCH = APP / "patches" / "v1_0" / "add_agent_attendance_indexes.py"
CHAT_INDEX_PATCH = APP / "patches" / "v1_0" / "add_chat_lookup_indexes.py"
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
    def test_scheduler_events_use_frappe_hook_name(self):
        self.assertFalse(hasattr(app_hooks, "scheduled_events"))
        self.assertEqual(
            app_hooks.scheduler_events["hourly"],
            [
                "vobiz_click_to_call.services.cdr.enqueue_recent_cdr_sync",
                "vobiz_click_to_call.services.cdr.enqueue_missing_inbound_cdr_sync",
                "vobiz_click_to_call.api.console.close_stale_agent_attendance_sessions",
            ],
        )

    def test_stale_ringing_recovery_is_bounded_and_bulk(self):
        cdr = CDR.read_text(encoding="utf-8")

        self.assertIn(
            "vobiz_click_to_call.services.cdr.recover_stale_ringing_calls",
            app_hooks.scheduler_events["cron"]["* * * * *"],
        )
        self.assertIn("STALE_RINGING_TIMEOUT_SECONDS = 60", cdr)
        self.assertIn('"Agent Ringing"', cdr)
        self.assertIn("STALE_RINGING_TIMEOUT", cdr)
        self.assertIn("stale-local-timeout", cdr)
        self.assertIn("limit_page_length=STALE_RINGING_RECOVERY_LIMIT", cdr)
        self.assertIn("UPDATE `tabVobiz Call Log`", cdr)
        self.assertIn("UPDATE `tabVobiz User Mapping`", cdr)

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
        self.assertIn("additions = []", index_patch)
        self.assertEqual(index_patch.count("alter table"), 1)

        attendance_patch = ATTENDANCE_INDEX_PATCH.read_text(encoding="utf-8")
        self.assertIn("additions = []", attendance_patch)
        self.assertEqual(attendance_patch.count("alter table"), 1)

        chat_patch = CHAT_INDEX_PATCH.read_text(encoding="utf-8")
        self.assertIn("vobiz_click_to_call.patches.v1_0.add_chat_lookup_indexes", patches)
        self.assertIn("idx_chat_conversation_reference_modified", chat_patch)
        self.assertIn("idx_chat_conversation_crm_lead_modified", chat_patch)
        self.assertIn("idx_chat_conversation_contact_modified", chat_patch)
        self.assertIn("algorithm=inplace, lock=none", chat_patch)

    def test_analytics_queries_are_bounded(self):
        console = CONSOLE.read_text(encoding="utf-8")

        self.assertIn("ANALYTICS_MAX_DAYS = 31", console)
        self.assertIn("ANALYTICS_CACHE_SECONDS = 30", console)
        self.assertIn("calls_only: int | str = 0", console)
        self.assertNotIn("limit_page_length=50000", console)
        self.assertNotIn("limit_page_length=10000", console)
        self.assertNotIn("_visible_crm_lead_names", console)

    def test_status_analytics_avoid_non_indexable_wildcards(self):
        console = CONSOLE.read_text(encoding="utf-8")
        start = console.index("\ndef _analytics_bucket_sql(")
        end = console.index("\ndef _analytics_recording_duration_sql(", start)
        bucket_sql = console[start:end].lower()

        self.assertNotIn(" like ", bucket_sql)
        self.assertNotIn("lower(", bucket_sql)
        self.assertLess(bucket_sql.index("`dial_status` in ('busy'"), bucket_sql.index("`billsec` > 0"))

    def test_runtime_cleanup_uses_set_based_updates(self):
        console = CONSOLE.read_text(encoding="utf-8")
        cleanup = DELETE_CLEANUP.read_text(encoding="utf-8")
        start = console.index("\ndef close_stale_agent_attendance_sessions(")
        end = console.index("\ndef _close_attendance_row(", start)
        stale_cleanup = console[start:end]

        self.assertIn("update `tabVobiz Agent Attendance Log`", stale_cleanup)
        self.assertNotIn("frappe.get_all(", stale_cleanup)
        self.assertNotIn("frappe.get_all(", cleanup)
        self.assertIn("update `tabVobiz User Mapping`", cleanup)

    def test_recording_worker_does_not_discard_pending_changes(self):
        recording = RECORDING_SERVICE.read_text(encoding="utf-8")

        self.assertNotIn("doc.reload()", recording)
        self.assertIn('"recording_status": "Starting"', recording)
        self.assertIn("frappe.db.commit()", recording)
        self.assertLess(recording.index('"recording_status": "Starting"'), recording.index("start_call_recording"))


if __name__ == "__main__":
    unittest.main()
