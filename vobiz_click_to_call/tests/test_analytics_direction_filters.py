from __future__ import annotations

import unittest
from pathlib import Path

from vobiz_click_to_call.api.console import (
    _analytics_team_member_users,
    _analytics_bucket_filter_sql,
    _analytics_status_filter,
    _summary_from_bucket_rows,
)
import frappe
from unittest.mock import patch


class TestAnalyticsDirectionFilters(unittest.TestCase):
    @patch("vobiz_click_to_call.api.console.frappe")
    def test_selected_teams_resolve_to_team_members_and_mapping_users(self, frappe_mock):
        frappe_mock.db.exists.return_value = True
        get_all = frappe_mock.get_all
        get_all.side_effect = lambda doctype, **kwargs: {
            "Team": ["leader@example.com"],
            "Team User": ["member@example.com"],
            "Vobiz User Mapping": ["member@example.com", "mapped@example.com"],
        }[doctype]

        self.assertEqual(
            _analytics_team_member_users(["Team One"]),
            ["leader@example.com", "member@example.com", "mapped@example.com"],
        )
        for call in get_all.call_args_list:
            self.assertEqual(call.kwargs["filters"]["name" if call.args[0] == "Team" else "parent" if call.args[0] == "Team User" else "team"], ["in", ["Team One"]])

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

    def test_agent_performance_has_page_size_and_pagination_controls(self):
        page = (
            Path(__file__).resolve().parents[1]
            / "vobiz_click_to_call"
            / "page"
            / "vobiz_agent_analytics"
            / "vobiz_agent_analytics.js"
        ).read_text(encoding="utf-8")
        self.assertIn('data-role="agent-page-size"', page)
        self.assertIn('data-action="agent-page"', page)
        self.assertIn("rows.slice(start, start + pageSize)", page)
        self.assertIn("agent_page: 1", page)
        self.assertIn("agent_page_size: 10", page)

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
