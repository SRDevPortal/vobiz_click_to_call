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
AGENT_ANALYTICS_JS = (
    Path(__file__).resolve().parents[1]
    / "vobiz_click_to_call"
    / "page"
    / "vobiz_agent_analytics"
    / "vobiz_agent_analytics.js"
)
AGENT_ANALYTICS_JSON = (
    Path(__file__).resolve().parents[1]
    / "vobiz_click_to_call"
    / "page"
    / "vobiz_agent_analytics"
    / "vobiz_agent_analytics.json"
)
CONSOLE_API = Path(__file__).resolve().parents[1] / "api" / "console.py"
USER_MAPPING_JSON = (
    Path(__file__).resolve().parents[1]
    / "vobiz_click_to_call"
    / "doctype"
    / "vobiz_user_mapping"
    / "vobiz_user_mapping.json"
)
AGENT_ATTENDANCE_JSON = (
    Path(__file__).resolve().parents[1]
    / "vobiz_click_to_call"
    / "doctype"
    / "vobiz_agent_attendance_log"
    / "vobiz_agent_attendance_log.json"
)
PUBLIC_JS = Path(__file__).resolve().parents[1] / "public" / "js"
AVAILABILITY_JS = PUBLIC_JS / "availability.js"


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

    def test_queue_selection_survives_refresh_for_autodial(self):
        row_html_source = self.method_source("row_html", "is_patient_queue")
        update_source = self.method_source("update_selected_count", "render_auto_toggle")
        start_source = self.method_source("start_auto_dial", "stop_auto_dial")

        self.assertIn("selected_queue_keys: new Set()", self.source)
        self.assertIn("this.state.selected_queue_keys.add(key)", self.source)
        self.assertIn("this.state.selected_queue_keys.delete(key)", self.source)
        self.assertIn("this.prune_selected_queue_keys()", self.source)
        self.assertIn("this.state.selected_queue_keys.has(this.queue_row_key(row))", row_html_source)
        self.assertIn("selected_queue_rows()", update_source)
        self.assertIn("sync_check_all_state()", update_source)
        self.assertIn("const rows = this.selected_queue_rows()", start_source)

    def test_lead_queue_shows_lead_owner_column(self):
        render_source = self.method_source("render", "inject_styles")
        visible_rows_source = self.method_source("visible_queue_rows", "render_queue_meta")
        meta_source = self.method_source("render_queue_meta", "queue_colspan")
        colspan_source = self.method_source("queue_colspan", "reset_filter_group_if_doctype_changed")
        row_html_source = self.method_source("row_html", "is_patient_queue")

        self.assertIn("Lead Owner", render_source)
        self.assertIn("Missed Call", render_source)
        self.assertIn("vobiz-lead-owner-col", render_source)
        self.assertIn("row.owner", visible_rows_source)
        self.assertIn("row.missed_call_status", visible_rows_source)
        self.assertIn(".vobiz-lead-owner-col", meta_source)
        self.assertIn("=== 'Patient'", meta_source)
        self.assertIn("? 14 : 13", colspan_source)
        self.assertIn("row.owner || ''", row_html_source)
        self.assertIn("missed_call_cell_html(row)", row_html_source)

    def test_agent_console_missed_calls_are_inbound_only(self):
        source = CONSOLE_API.read_text(encoding="utf-8")
        start = source.index("\ndef _attach_queue_missed_calls(")
        end = source.index("\ndef _sort_queue_by_missed_calls(", start)
        missed_source = source[start:end]

        self.assertIn("is_inbound_missed_call(call)", missed_source)
        self.assertEqual(missed_source.count('"direction": "Incoming"'), 2)
        self.assertIn('"direction",', missed_source)
        self.assertIn('"billsec",', missed_source)
        self.assertIn('"recording_duration",', missed_source)

    def test_agent_console_missed_call_count_opens_list(self):
        bind_source = self.method_source("bind", "load")
        cell_source = self.method_source("missed_call_cell_html", "is_recent_missed_call")
        open_source = self.method_source("open_missed_calls", "open_queue_whatsapp")
        console = CONSOLE_API.read_text(encoding="utf-8")

        self.assertIn('data-action="open-missed-calls"', cell_source)
        self.assertIn('type="button"', cell_source)
        self.assertIn("this.open_missed_calls", bind_source)
        self.assertIn("get_reference_missed_calls", open_source)
        self.assertIn("missed_call_list_html", open_source)
        self.assertIn("def get_reference_missed_calls", console)
        self.assertIn("def _reference_missed_call_rows", console)

    def test_only_new_missed_call_bubbles_are_prioritized(self):
        filtered_source = self.method_source("filtered_queue_rows", "paginated_queue_rows")
        console = CONSOLE_API.read_text(encoding="utf-8")
        start = console.index("\ndef _attach_queue_missed_calls(")
        end = console.index("\ndef _sort_queue_by_missed_calls(", start)
        missed_source = console[start:end]

        self.assertIn("new_missed_calls_first(rows)", filtered_source)
        self.assertIn("this.is_new_missed_call(a)", filtered_source)
        self.assertIn("return aNew ? -1 : 1", filtered_source)
        self.assertIn("queue_sort_by: 'creation_desc'", self.source)
        self.assertIn('option value="creation_desc" selected', self.source)
        self.assertIn("this.state.queue_sort_by || this.page.main.find", self.source)
        self.assertIn('options.get(sort_key, options["creation_desc"])', console)
        self.assertIn("return rows", missed_source)
        self.assertNotIn("return _sort_queue_by_missed_calls(rows)", missed_source)

    def test_agent_analytics_missed_filter_is_inbound_only(self):
        source = CONSOLE_API.read_text(encoding="utf-8")
        start = source.index("\ndef _analytics_bucket_filter_sql(")
        end = source.index("\ndef _analytics_summary_sql(", start)
        filter_source = source[start:end]
        data_start = source.index("\ndef _analytics_data(")
        data_end = source.index("\ndef _analytics_sql_conditions(", data_start)
        data_source = source[data_start:data_end]
        performance_start = source.index("\ndef _performance_summary(")
        performance_end = source.index("\ndef _performance_by_user(", performance_start)
        performance_source = source[performance_start:performance_end]

        self.assertIn("and {direction} = 'Incoming'", filter_source)
        self.assertIn('"direction"', data_source)
        self.assertIn('row.get("direction") == "Incoming"', performance_source)
        self.assertNotIn('bucket_filter.replace("bucket"', source)

    def test_agent_analytics_connected_requires_talk_time(self):
        source = CONSOLE_API.read_text(encoding="utf-8")
        start = source.index("\ndef _analytics_bucket_sql(")
        end = source.index("\ndef _analytics_recording_duration_sql(", start)
        bucket_source = source[start:end]

        self.assertIn("coalesce(`billsec`, 0) > 0 then 'connected'", bucket_source)
        self.assertNotIn("`status` in ('Connected', 'Completed') then 'connected'", bucket_source)

    def test_agent_analytics_talk_time_starts_from_customer_answer(self):
        source = CONSOLE_API.read_text(encoding="utf-8")
        start = source.index("\ndef _analytics_talk_sql(")
        end = source.index("\ndef _analytics_unique_key_sql(", start)
        talk_source = source[start:end]

        self.assertIn("`call_flow` = 'Customer First'", talk_source)
        self.assertIn("timestampdiff(second, `answer_time`, `end_time`)", talk_source)
        self.assertIn("`call_flow` = 'Agent First'", talk_source)
        self.assertIn("coalesce(`duration`, 0) > 0", talk_source)
        self.assertNotIn("nullif({_analytics_recording_duration_sql()}, 0)", talk_source)
        self.assertNotIn("nullif(`billsec`, 0)", talk_source)

    def test_agent_online_time_uses_persistent_attendance_log(self):
        source = CONSOLE_API.read_text(encoding="utf-8")
        attendance = AGENT_ATTENDANCE_JSON.read_text(encoding="utf-8")
        hooks = (Path(__file__).resolve().parents[1] / "hooks.py").read_text(encoding="utf-8")

        self.assertIn("AGENT_ATTENDANCE_DOCTYPE = \"Vobiz Agent Attendance Log\"", source)
        self.assertIn("_open_or_touch_attendance_session(user, tab_id, now)", source)
        self.assertIn("_close_attendance_session(user, tab_id, _now_ist())", source)
        self.assertIn("def _persistent_attendance_snapshot", source)
        self.assertIn("Vobiz Agent Attendance Log", attendance)
        self.assertIn('"agent_user"', attendance)
        self.assertIn('"online_from"', attendance)
        self.assertIn('"offline_at"', attendance)
        self.assertIn("vobiz_click_to_call.api.console.close_stale_agent_attendance_sessions", hooks)

    def test_agent_attendance_is_hard_disabled(self):
        attendance_service = (
            Path(__file__).resolve().parents[1] / "services" / "attendance.py"
        ).read_text(encoding="utf-8")
        console = CONSOLE_API.read_text(encoding="utf-8")
        call_api = (Path(__file__).resolve().parents[1] / "api" / "call.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("AGENT_ATTENDANCE_ENABLED = False", attendance_service)
        self.assertIn("agent_attendance_enabled() and bool(", console)
        self.assertIn("not agent_attendance_enabled()", call_api)

    def test_agent_desk_activity_tracks_online_time_without_call_availability(self):
        source = CONSOLE_API.read_text(encoding="utf-8")
        hooks = (Path(__file__).resolve().parents[1] / "hooks.py").read_text(encoding="utf-8")
        availability = AVAILABILITY_JS.read_text(encoding="utf-8")
        activity_start = source.index("\ndef record_agent_activity(")
        activity_end = source.index("\n\n@frappe.whitelist", activity_start + 1)
        activity_source = source[activity_start:activity_end]
        inactive_start = source.index("\ndef mark_agent_activity_inactive(")
        inactive_end = source.index("\n\n@frappe.whitelist", inactive_start + 1)
        inactive_source = source[inactive_start:inactive_end]

        self.assertIn("/assets/vobiz_click_to_call/js/availability.js", hooks)
        self.assertIn("def record_agent_activity", source)
        self.assertIn("def mark_agent_activity_inactive", source)
        self.assertIn("_open_or_touch_attendance_session(user, tab_id, now, source=\"Desk Activity\")", source)
        self.assertIn("record_agent_activity", availability)
        self.assertIn("mark_agent_activity_inactive", availability)
        self.assertIn("ACTIVITY_HEARTBEAT_MS = 30 * 1000", availability)
        self.assertIn("ACTIVITY_IDLE_MS = 5 * 60 * 1000", availability)
        self.assertIn("sessionStorage", availability)
        self.assertNotIn("availability_status", activity_source)
        self.assertNotIn("accept_calls", activity_source)
        self.assertNotIn("availability_status", inactive_source)
        self.assertNotIn("accept_calls", inactive_source)

    def test_queue_owner_uses_only_lead_owner_field(self):
        source = CONSOLE_API.read_text(encoding="utf-8")
        start = source.index("\ndef _reference_row(")
        end = source.index("\ndef _call_history(", start)
        reference_source = source[start:end]

        self.assertIn('"owner": data.get("lead_owner")', reference_source)
        self.assertNotIn('data.get("created_by_agent") or data.get("owner")', reference_source)

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
        self.assertIn(".btn-modal-primary", update_source)
        self.assertIn("btn-danger", update_source)
        self.assertIn("update_workdesk_header_call_action", update_source)
        self.assertIn('data-workdesk-action="call"', self.source)
        self.assertIn("return this.handle_workdesk_primary_action(row)", self.source)
        self.assertIn("this.update_workdesk_primary_action(row)", render_live_source)

    def test_workdesk_stop_and_terminal_call_prompt_disposition(self):
        cancel_source = self.method_source("cancel_call_log", "maybe_prompt_workdesk_disposition")
        prompt_source = self.method_source("maybe_prompt_workdesk_disposition", "open_post_call_disposition_dialog")
        dialog_source = self.method_source("open_post_call_disposition_dialog", "save_disposition")
        refresh_source = self.method_source("refresh_workdesk_live_call", "live_call_steps")

        self.assertIn("get_call_status", cancel_source)
        self.assertIn("this.clear_tracked_live_call(call_log)", cancel_source)
        self.assertIn("this.state.active_call = { last_call: call }", cancel_source)
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

    def test_workdesk_whatsapp_lazy_loads_and_sends_inline(self):
        console = CONSOLE_API.read_text(encoding="utf-8")
        refresh_source = self.method_source("refresh_inline_whatsapp", "load_more_whatsapp_messages")
        more_source = self.method_source("load_more_whatsapp_messages", "send_workdesk_whatsapp")
        send_source = self.method_source("send_workdesk_whatsapp", "insert_workdesk_emoji")
        whatsapp_source = self.method_source("workdesk_whatsapp_html", "workdesk_whatsapp_messages_html")
        media_source = self.method_source("workdesk_whatsapp_media_html", "workdesk_whatsapp_composer_html")

        self.assertIn("VOBIZ_WHATSAPP_PAGE_SIZE = 30", self.source)
        self.assertIn("data-wa-loader", self.source)
        self.assertIn("e.key === 'Enter' && !e.shiftKey", self.source)
        self.assertIn(".vobiz-detail-dialog, .vobiz-detail-dialog *", self.source)
        self.assertIn(".vobiz-wa-image", self.source)
        self.assertIn("limit: VOBIZ_WHATSAPP_PAGE_SIZE", refresh_source)
        self.assertIn("limit: VOBIZ_WHATSAPP_PAGE_SIZE", more_source)
        self.assertIn("vobiz_click_to_call.api.console.send_whatsapp_reply", send_source)
        self.assertIn("data-wa-template", self.source)
        self.assertIn("open_workdesk_template_dialog", self.source)
        self.assertIn("show_workdesk_template_dialog", self.source)
        self.assertIn("vobiz_click_to_call.api.console.get_whatsapp_templates", self.source)
        self.assertIn("vobiz_click_to_call.api.console.send_whatsapp_template", self.source)
        self.assertIn("workdesk_whatsapp_message_body_text", self.source)
        self.assertIn("workdesk_whatsapp_media_url", self.source)
        self.assertIn("img class=\"vobiz-wa-image\"", media_source)
        self.assertNotIn("__('Conversation')", whatsapp_source)
        self.assertNotIn("data.unread_count", whatsapp_source)
        self.assertNotIn("data.lead_temperature", whatsapp_source)
        self.assertIn("def send_whatsapp_reply", console)
        self.assertIn("def get_whatsapp_templates", console)
        self.assertIn("def send_whatsapp_template", console)
        self.assertIn("def get_whatsapp_messages(conversation: str, limit: int | str = 30", console)
        self.assertIn("_whatsapp_messages_page(conversation, 30)", console)
        self.assertIn('"attachment_file"', console)
        self.assertIn('row["attachment_url"]', console)

    def test_workdesk_vobiz_shows_audio_only(self):
        vobiz_source = self.method_source("workdesk_vobiz_html", "workdesk_whatsapp_html")
        audio_source = self.method_source("detail_audio_html", "audio_player_html")

        self.assertIn("Audio Recordings", vobiz_source)
        self.assertIn("detail_audio_html(rows)", vobiz_source)
        self.assertNotIn("detail_transcript_tabs_html(rows)", vobiz_source)
        self.assertNotIn("__('Transcript')", vobiz_source)
        self.assertNotIn("Transcript / Audio", vobiz_source)
        self.assertIn("this.audio_player_html(row)", audio_source)

    def test_lead_dispositions_are_contextual_in_console(self):
        select_source = self.method_source("select_row", "render_focus")
        call_row_source = self.method_source("call_row", "detail_key")
        apply_source = self.method_source("apply_context_dispositions", "show_tab")
        disposition_source = self.method_source("workdesk_lead_disposition_html", "render_workdesk_live_call")
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
        self.assertIn("row.doctype !== 'CRM Lead'", disposition_source)

    def test_manual_disposition_saves_status_and_sr_lead_disposition(self):
        render_source = self.method_source("render_dispositions", "apply_context_dispositions")
        visibility_source = self.method_source("render_manual_disposition_visibility", "apply_context_dispositions")
        save_source = self.method_source("save_disposition", "open_reference")

        self.assertIn("Select Status", render_source)
        self.assertIn("Select Lead Disposition", render_source)
        self.assertIn("manual-disposition-section", self.source)
        self.assertIn("ai_disposition_enabled", visibility_source)
        self.assertIn("leadStatus", save_source)
        self.assertIn("lead_status: leadStatus", save_source)
        self.assertIn("Select status.", save_source)

    def test_ai_mode_hides_manual_disposition_prompt(self):
        prompt_source = self.method_source("maybe_prompt_workdesk_disposition", "open_post_call_disposition_dialog")
        dialog_source = self.method_source("open_post_call_disposition_dialog", "save_disposition")

        self.assertIn("if (this.state.ai_disposition_enabled) return", prompt_source)
        self.assertIn("if (this.state.ai_disposition_enabled) return", dialog_source)
        self.assertIn("render_manual_disposition_visibility", self.source)

    def test_mapping_drives_lead_or_patient_queue(self):
        console = CONSOLE_API.read_text(encoding="utf-8")
        mapping_json = USER_MAPPING_JSON.read_text(encoding="utf-8")

        self.assertIn('"fieldname": "queue_source"', mapping_json)
        self.assertIn('"options": "CRM Lead\\nPatient\\nCRM Lead and Patient\\nDiscontinued"', mapping_json)
        self.assertIn('"fieldname": "sr_medical_department"', mapping_json)
        self.assertIn('"fieldname": "sr_followup_id"', mapping_json)
        self.assertIn("QUEUE_SOURCE_DOCTYPES", console)
        self.assertIn('"Patient": "Patient"', console)
        self.assertIn('"Discontinued": "CRM Lead"', console)
        self.assertIn('"queue_meta": _queue_meta(queue_source, queue_doctype, agent_queue_source=agent_queue_source)', console)
        self.assertIn('fetch_rows = frappe.get_list if doctype == "CRM Lead" else frappe.get_all', console)
        self.assertIn('filters["sr_medical_department"]', console)
        self.assertIn('filters["sr_followup_id"]', console)
        self.assertIn('filters["sr_followup_day"]', console)
        self.assertIn('filters["vobiz_last_call_status"]', console)
        self.assertIn("this.state.queue_meta = Object.assign", self.source)
        self.assertIn("queue_meta_value('summary_tab_label')", self.source)

    def test_console_static_context_is_cached_and_queue_limit_is_capped(self):
        console = CONSOLE_API.read_text(encoding="utf-8")

        self.assertIn("CONSOLE_STATIC_CONTEXT_TTL_SECONDS = 60", console)
        self.assertIn("CONSOLE_QUEUE_LIMIT_MAX = 100", console)
        self.assertIn("min(frappe.utils.cint(limit) or 25, CONSOLE_QUEUE_LIMIT_MAX)", console)
        self.assertIn("static_context = _get_console_static_context", console)
        self.assertIn('frappe.cache().get_value(cache_key)', console)
        self.assertIn("expires_in_sec=CONSOLE_STATIC_CONTEXT_TTL_SECONDS", console)
        self.assertNotIn("min(frappe.utils.cint(limit) or 25, 500)", console)

    def test_agent_console_dashboard_uses_filtered_analytics(self):
        console = CONSOLE_API.read_text(encoding="utf-8")
        analytics = AGENT_ANALYTICS_JS.read_text(encoding="utf-8")
        analytics_json = AGENT_ANALYTICS_JSON.read_text(encoding="utf-8")

        self.assertIn("def get_analytics(", console)
        self.assertIn("agent_user: str | None = None", console)
        self.assertIn("include_calls: int | str = 0", console)
        self.assertIn("call_limit: int | str = 50", console)
        self.assertIn('"calls_loaded": include_call_rows', console)
        self.assertIn('"has_more_calls": include_call_rows', console)
        self.assertIn('"recording_url"', console)
        self.assertIn('"recording_download_url": recording_proxy_url', console)
        self.assertIn('"team_options": _analytics_team_options(queue_source, visible_leads=visible_crm_leads)', console)
        self.assertIn('"department_options": _analytics_department_options(queue_source)', console)
        self.assertIn('"agent_options": _analytics_agent_options(', console)
        self.assertIn('queue_source=queue_source', console)
        self.assertIn('team=team', console)
        self.assertIn("department: str | None = None", console)
        self.assertIn("_apply_visible_crm_lead_scope(filters)", console)
        self.assertIn("_apply_crm_lead_analytics_filters(", console)
        self.assertIn("_apply_patient_department_analytics_filter(filters, department=department)", console)
        self.assertIn("frappe.get_list(", console)
        self.assertIn('"crm_lead_reference_names"', console)
        self.assertIn('"team_leader": team_leader', console)
        self.assertIn("def _analytics_agent_options", console)
        self.assertIn("def _analytics_team_options", console)
        self.assertIn("def _analytics_department_options", console)
        self.assertIn("def _crm_lead_distinct_options", console)
        self.assertIn('"sr_medical_department"', console)
        self.assertIn('"lead_owner"', console)
        self.assertIn('lead.`lead_owner` as analytics_agent', console)
        self.assertIn("def _can_view_all_analytics_agents", console)
        self.assertIn('"Call Center Manager"', console)
        self.assertIn('"Vobiz Manager"', console)
        self.assertIn('"agents": _analytics_agents_sql(conditions, params, status_filter=status_filter, queue_source=queue_source)', console)
        self.assertIn("def _analytics_data(", console)
        self.assertIn("def _analytics_summary_sql", console)
        self.assertIn("def _analytics_call_rows_sql", console)
        self.assertIn("ANALYTICS_STATUS_OPTIONS", console)
        self.assertIn('"status_breakdown": _analytics_status_breakdown_sql(conditions, params)', console)
        self.assertIn('"outcome_breakdown": _analytics_outcome_breakdown_sql(conditions, params)', console)
        self.assertIn('"daily": _analytics_daily_sql(conditions, params, from_date, to_date)', console)
        self.assertIn("vobiz-agent-analytics", analytics_json)
        self.assertIn("frappe.pages['vobiz-agent-analytics']", analytics)
        self.assertIn("render_daily_chart", analytics)
        self.assertIn("render_agent_chart", analytics)
        self.assertIn('data-role="team"', analytics)
        self.assertIn('data-role="department"', analytics)
        self.assertIn('data.team_options', analytics)
        self.assertIn('data.department_options', analytics)
        self.assertIn("data.is_admin || data.is_team_leader", analytics)
        self.assertIn("All Team Agents", analytics)
        self.assertIn("vobiz-daily-chart", analytics)
        self.assertIn("vobiz-axis-chart", analytics)
        self.assertIn("vobiz-agent-card", analytics)
        self.assertIn("agent_card_html", analytics)
        self.assertIn("schedule_load", analytics)
        self.assertIn("load_calls", analytics)
        self.assertIn("render_calls_placeholder", analytics)
        self.assertIn("clear_filters", analytics)
        self.assertIn("Clear All Filters", analytics)
        self.assertIn('data-action="clear-filters"', analytics)
        self.assertNotIn("Apply Filters", analytics)
        self.assertNotIn('data-action="apply"', analytics)
        self.assertIn("include_calls: 0", analytics)
        self.assertIn("include_calls: 1", analytics)
        self.assertIn('data-action="load-calls"', analytics)
        self.assertIn('data-action="load-more-calls"', analytics)
        self.assertIn('data-action="play-recording"', analytics)
        self.assertIn('data-action="stop-recording"', analytics)
        self.assertIn('data-role="recording-time"', analytics)
        self.assertIn("recording_button_html", analytics)
        self.assertIn("play_recording", analytics)
        self.assertIn("stop_recording", analytics)
        self.assertIn("format_audio_time", analytics)
        self.assertIn("vobiz-spectrum", analytics)
        self.assertIn("vobiz-spectrum-pulse", analytics)
        self.assertIn("Recording", analytics)
        self.assertIn("recording_download_url", analytics)
        self.assertIn('data-role="agent-user"', analytics)
        self.assertIn("All Agents", analytics)
        self.assertNotIn("Status Totals", analytics)
        self.assertNotIn("Status Breakdown", analytics)
        self.assertIn('data-role="calls"', analytics)
        self.assertIn("connected calls only", analytics)
        self.assertIn("frappe.set_route('vobiz-agent-analytics')", self.source)
        self.assertNotIn('data-role="analytics-from-date"', self.source)

    def test_global_desk_scripts_do_not_call_vobiz_apis_on_home(self):
        click_to_call = (PUBLIC_JS / "click_to_call.js").read_text(encoding="utf-8")
        list_dialer = (PUBLIC_JS / "list_dialer.js").read_text(encoding="utf-8")

        self.assertIn("function shouldLoadAllowedDoctypes()", click_to_call)
        self.assertIn('window.location.pathname === "/app/home"', click_to_call)
        self.assertIn('route[0] === "Form" || route[0] === "List"', click_to_call)
        self.assertIn("if (allowedDoctypesLoaded || !shouldLoadAllowedDoctypes()) return", click_to_call)
        self.assertIn("function shouldInstall()", list_dialer)
        self.assertIn('window.location.pathname === "/app/home"', list_dialer)
        self.assertIn('return route[0] === "List"', list_dialer)
        self.assertIn("if (installed || !shouldInstall()) return", list_dialer)


if __name__ == "__main__":
    unittest.main()
