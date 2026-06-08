from __future__ import annotations

from pathlib import Path
import unittest


AGENT_CONSOLE_JS = (
    Path(__file__).resolve().parents[1]
    / "vobiz_click_to_call"
    / "page"
    / "vobiz_agent_console"
    / "vobiz_agent_console.js"
)


class TestAgentConsoleAutoDial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = AGENT_CONSOLE_JS.read_text(encoding="utf-8")

    def method_source(self, method_name: str, next_method_name: str) -> str:
        start = self.source.index(f"\n\t{method_name}(")
        end = self.source.index(f"\n\t{next_method_name}(", start)
        return self.source[start:end]

    def test_terminal_status_helper_covers_autodial_end_states(self):
        self.assertIn("is_terminal_status(status)", self.source)
        for status in ("Completed", "Failed", "Busy", "No Answer", "Cancelled", "Canceled"):
            with self.subTest(status=status):
                self.assertIn(f"'{status}'", self.source)

    def test_api_terminal_statuses_include_vobiz_ai_canceled_spelling(self):
        source = (
            Path(__file__).resolve().parents[1] / "api" / "call.py"
        ).read_text(encoding="utf-8")

        self.assertIn('"Canceled"', source)

    def test_final_autodial_call_clears_live_state_and_timer(self):
        finish_source = self.method_source("finish_auto_dial_call", "auto_call_outcome")

        self.assertIn("session.current = null", finish_source)
        self.assertIn("session.in_flight = false", finish_source)
        self.assertIn("this.state.call_started_at = null", finish_source)
        self.assertIn("this.clear_tracked_live_call(call.name)", finish_source)
        self.assertIn("this.stop_timer()", finish_source)

    def test_render_active_call_stops_timer_without_active_non_terminal_call(self):
        render_source = self.method_source("render_active_call", "render_call_assets")

        self.assertIn("active.name && !this.is_terminal_status(active.status)", render_source)
        self.assertIn("this.stop_timer()", render_source)
        self.assertIn("this.clear_tracked_live_call(last.name)", render_source)

    def test_details_click_uses_lightweight_context_once(self):
        call_row_source = self.method_source("call_row", "open_detail_dialog")
        select_row_source = self.method_source("select_row", "render_focus")

        self.assertNotIn("this.select_row(index)", call_row_source)
        self.assertIn("lite: 1", call_row_source)
        self.assertIn("lite: 1", select_row_source)
        self.assertIn("this.open_detail_dialog(row", call_row_source)

    def test_details_button_shows_loader_and_blocks_repeat_clicks(self):
        row_html_source = self.method_source("row_html", "update_selected_count")
        call_row_source = self.method_source("call_row", "detail_key")
        loading_source = self.method_source("set_detail_loading", "open_detail_dialog")

        self.assertIn("detail_loading_key", self.source)
        self.assertIn("fa-spinner fa-spin", row_html_source)
        self.assertIn("disabled", row_html_source)
        self.assertIn("if (this.state.detail_loading_key) return", call_row_source)
        self.assertIn("this.set_detail_loading(row, true)", call_row_source)
        self.assertIn("request.always(() => this.set_detail_loading(row, false))", call_row_source)
        self.assertIn("this.render_queue()", loading_source)

    def test_workdesk_primary_button_toggles_start_and_stop_call(self):
        open_dialog_source = self.method_source("open_detail_dialog", "handle_workdesk_primary_action")
        primary_source = self.method_source("handle_workdesk_primary_action", "update_workdesk_primary_action")
        update_source = self.method_source("update_workdesk_primary_action", "load_workdesk_tab")
        render_live_source = self.method_source("render_workdesk_live_call", "workdesk_live_call_html")

        self.assertIn("primary_action: () => this.handle_workdesk_primary_action(row)", open_dialog_source)
        self.assertIn("this.cancel_call_log(call.name, row)", primary_source)
        self.assertIn("this.start_call_for_row(row)", primary_source)
        self.assertIn("matching_active_call(row)", update_source)
        self.assertIn("Stop Call", update_source)
        self.assertIn("Start Call", update_source)
        self.assertIn("btn-danger", update_source)
        self.assertIn("this.update_workdesk_primary_action(row)", render_live_source)

    def test_workdesk_stop_and_terminal_call_prompt_disposition(self):
        cancel_source = self.method_source("cancel_call_log", "maybe_prompt_workdesk_disposition")
        prompt_source = self.method_source("maybe_prompt_workdesk_disposition", "open_post_call_disposition_dialog")
        dialog_source = self.method_source("open_post_call_disposition_dialog", "save_disposition")
        refresh_source = self.method_source("refresh_workdesk_live_call", "live_call_steps")

        self.assertIn("get_call_status", cancel_source)
        self.assertIn("this.clear_tracked_live_call(call_log)", cancel_source)
        self.assertIn("this.update_workdesk_primary_action", cancel_source)
        self.assertIn("this.maybe_prompt_workdesk_disposition(call)", cancel_source)
        self.assertIn("this.maybe_prompt_workdesk_disposition(call)", refresh_source)
        self.assertIn("disposition_prompted_call_log", prompt_source)
        self.assertIn("Complete Call Disposition", dialog_source)
        self.assertIn("AI Suggestion", dialog_source)
        self.assertIn("lead_status", dialog_source)
        self.assertIn("get_lead_disposition_context_api", dialog_source)
        self.assertIn("save_disposition", dialog_source)

    def test_workdesk_heavy_tabs_are_lazy_loaded(self):
        load_tab_source = self.method_source("load_workdesk_tab", "workdesk_lead_html")

        self.assertIn("get_workdesk_tab", load_tab_source)
        for tab in ("encounters", "clinical-history", "reports", "vobiz", "whatsapp"):
            with self.subTest(tab=tab):
                self.assertIn(f"'{tab}'", load_tab_source)
        self.assertIn("context.loaded_workdesk_tabs[tab] = true", load_tab_source)

    def test_lead_dispositions_are_contextual_in_console(self):
        select_source = self.method_source("select_row", "render_focus")
        call_row_source = self.method_source("call_row", "detail_key")
        apply_source = self.method_source("apply_context_dispositions", "show_tab")
        refresh_source = self.method_source("refresh_lead_disposition_options", "active_disposition_reference")
        workdesk_source = self.method_source("workdesk_lead_disposition_html", "render_workdesk_live_call")

        self.assertIn("this.apply_context_dispositions(this.state.context)", select_source)
        self.assertIn("this.apply_context_dispositions(this.state.context)", call_row_source)
        self.assertIn("lead_disposition", apply_source)
        self.assertIn("lead_disposition_context", apply_source)
        self.assertIn("this.render_dispositions()", apply_source)
        self.assertIn("lead-status", self.source)
        self.assertIn("get_lead_disposition_context_api", refresh_source)
        self.assertIn("Lead Disposition", workdesk_source)
        self.assertIn("Available for this lead", workdesk_source)

    def test_manual_disposition_saves_status_and_sr_lead_disposition(self):
        render_source = self.method_source("render_dispositions", "apply_context_dispositions")
        save_source = self.method_source("save_disposition", "open_reference")

        self.assertIn("Select CRM Status", render_source)
        self.assertIn("Select SR Lead Disposition", render_source)
        self.assertIn("leadStatus", save_source)
        self.assertIn("lead_status: leadStatus", save_source)
        self.assertIn("Select CRM status, SR lead disposition, and add notes", save_source)


if __name__ == "__main__":
    unittest.main()
