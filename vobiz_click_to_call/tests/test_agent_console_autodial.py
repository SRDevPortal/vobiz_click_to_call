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


if __name__ == "__main__":
    unittest.main()
