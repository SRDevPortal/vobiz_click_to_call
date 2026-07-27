from __future__ import annotations

import unittest
from pathlib import Path

from vobiz_click_to_call.api.console import (
    _analytics_bucket_filter_sql,
    _analytics_status_filter,
    _summary_from_bucket_rows,
)
import frappe


class TestAnalyticsDirectionFilters(unittest.TestCase):
    def test_connected_inbound_filter(self):
        self.assertEqual(_analytics_status_filter("connected_inbound"), "connected_inbound")
        self.assertEqual(
            _analytics_bucket_filter_sql("connected_inbound"),
            "where bucket = 'connected' and `direction` = 'Incoming'",
        )

    def test_connected_outbound_filter(self):
        self.assertEqual(_analytics_status_filter("connected_outbound"), "connected_outbound")
        self.assertEqual(
            _analytics_bucket_filter_sql("connected_outbound"),
            "where bucket = 'connected' and `direction` = 'Outgoing'",
        )

    def test_analytics_page_exposes_both_filters(self):
        page = (
            Path(__file__).resolve().parents[1]
            / "vobiz_click_to_call"
            / "page"
            / "vobiz_agent_analytics"
            / "vobiz_agent_analytics.js"
        ).read_text(encoding="utf-8")
        status_dropdown = page[page.index('data-role="status-filter"'):page.index("</select>", page.index('data-role="status-filter"'))]
        self.assertNotIn('value="connected_inbound"', status_dropdown)
        self.assertNotIn('value="connected_outbound"', status_dropdown)
        self.assertIn("Connected Incoming", page)
        self.assertIn("Connected Outgoing", page)
        self.assertIn("status_filter: 'connected_inbound'", page)
        self.assertIn("status_filter: 'connected_outbound'", page)
        kpis = page[page.index("render_kpis(summary, data)"):page.index("render_calls(calls, append, data)")]
        self.assertNotIn("{ label: __('Connected'),", kpis)

    def test_summary_has_separate_connected_direction_counts(self):
        summary = _summary_from_bucket_rows(
            [
                frappe._dict(bucket="connected", direction="Incoming", call_count=2, talk_seconds=20, cost=0),
                frappe._dict(bucket="connected", direction="Outgoing", call_count=3, talk_seconds=30, cost=0),
            ]
        )
        self.assertEqual(summary["connected"], 5)
        self.assertEqual(summary["connected_inbound"], 2)
        self.assertEqual(summary["connected_outbound"], 3)
