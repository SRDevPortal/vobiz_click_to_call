from __future__ import annotations

import json
from pathlib import Path
import unittest


APP_ROOT = Path(__file__).resolve().parents[1]
BENCH_APPS = APP_ROOT.parents[1]
INBOUND_API = APP_ROOT / "api" / "inbound.py"
PATCHES = APP_ROOT / "patches.txt"
HOOKS = APP_ROOT / "hooks.py"
CDR_SERVICE = APP_ROOT / "services" / "cdr.py"
INCOMING_MAPPING_JSON = (
    APP_ROOT
    / "vobiz_click_to_call"
    / "doctype"
    / "vobiz_incoming_mapping"
    / "vobiz_incoming_mapping.json"
)
INCOMING_AGENT_JSON = (
    APP_ROOT
    / "vobiz_click_to_call"
    / "doctype"
    / "vobiz_incoming_mapping_agent"
    / "vobiz_incoming_mapping_agent.json"
)
DEDUPE_SETTINGS_JSON = (
    BENCH_APPS
    / "crm_lead_dedupe"
    / "crm_lead_dedupe"
    / "crm_lead_dedupe"
    / "doctype"
    / "crm_lead_dedupe_settings"
    / "crm_lead_dedupe_settings.json"
)
VOBIZ_SETTINGS_JSON = (
    APP_ROOT
    / "vobiz_click_to_call"
    / "doctype"
    / "vobiz_settings"
    / "vobiz_settings.json"
)


class TestIncomingMappingSource(unittest.TestCase):
    def setUp(self):
        self.inbound_source = INBOUND_API.read_text(encoding="utf-8")

    def test_assignment_and_status_routing_runs_before_previous_agent_lookup(self):
        previous_index = self.inbound_source.index("previous = find_last_customer_agent(customer_number)")
        unknown_index = self.inbound_source.index("reference_first = bool(")
        resolve_index = self.inbound_source.index("target = resolve_inbound_target(previous, settings)")

        self.assertLess(unknown_index, previous_index)
        self.assertLess(unknown_index, resolve_index)

    def test_unknown_route_keeps_existing_lead_and_patient_out(self):
        source = self.inbound_source

        self.assertIn("existing = find_existing_reference(customer_number)", source)
        self.assertIn('existing.get("doctype") == "Patient"', source)
        self.assertIn("route_existing_patient_inbound(existing[\"name\"], customer_number, did_number, payload, settings)", source)
        self.assertIn('existing.get("doctype") == "CRM Lead"', source)
        self.assertIn("route_existing_crm_lead_inbound(existing[\"name\"], customer_number, did_number, payload, settings)", source)
        self.assertIn("return None", source[source.index("def find_existing_reference"): source.index("def find_incoming_mapping")])
        self.assertIn('find_by_phone("Patient"', source)
        self.assertIn("find_latest_crm_lead_by_phone(customer_number)", source)

    def test_existing_reference_routes_before_last_called_agent(self):
        source = self.inbound_source
        route_start = source.index("def route():")
        route_end = source.index("def dial_action(", route_start)
        route_source = source[route_start:route_end]

        self.assertLess(
            route_source.index("reference_first = bool("),
            route_source.index("previous = find_last_customer_agent(customer_number)"),
        )

    def test_assignment_priority_is_configurable_in_vobiz_settings(self):
        settings = json.loads(VOBIZ_SETTINGS_JSON.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in settings["fields"]}
        field = fields["prefer_current_lead_assignment_for_incoming_calls"]

        self.assertEqual(field["fieldtype"], "Check")
        self.assertEqual(field["default"], "1")
        self.assertIn(
            "prefer_current_lead_assignment_for_incoming_calls(settings)",
            self.inbound_source,
        )
        self.assertIn("existing.get(\"treat_as_new\")", self.inbound_source)

    def test_treat_as_new_status_uses_dedicated_dedupe_setting(self):
        source = self.inbound_source
        settings = json.loads(DEDUPE_SETTINGS_JSON.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in settings["fields"]}

        field = fields["incoming_call_treat_as_new_statuses"]
        self.assertEqual(field["fieldtype"], "Table MultiSelect")
        self.assertEqual(field["options"], "CRM Lead Dedupe Merge Status")
        self.assertIn("lead.status in _incoming_call_treat_as_new_statuses()", source)
        self.assertIn('filters={fieldname: key}', source)
        self.assertNotIn('find_by_phone("CRM Lead"', source)

    def test_existing_patient_overrides_lead_owner_and_routes_by_department_followup(self):
        source = self.inbound_source
        existing_start = source.index("existing = find_existing_reference(customer_number)")
        patient_index = source.index('existing.get("doctype") == "Patient"', existing_start)
        lead_index = source.index('existing.get("doctype") == "CRM Lead"', existing_start)
        start = source.index("def resolve_patient_inbound_target")
        end = source.index("def resolve_lead_owner_inbound_target", start)
        patient_source = source[start:end]

        self.assertLess(patient_index, lead_index)
        self.assertIn("patient_route_mappings(patient)", patient_source)
        self.assertIn('"route_type": "patient_mapping"', patient_source)
        self.assertIn('"route_type": "patient_mapping_fallback_user"', patient_source)
        self.assertIn('"route_type": "patient_end_fallback_mobile"', patient_source)
        self.assertIn("_patient_mapping_matches(row, patient)", patient_source)
        self.assertIn('"sr_medical_departments"', patient_source)
        self.assertIn('"sr_followup_ids"', patient_source)
        self.assertIn('"sr_dpt_diseases"', patient_source)
        self.assertIn('"sr_dpt_languages"', patient_source)
        self.assertIn("patient_matches_mapping(patient, mapping)", patient_source)

    def test_existing_crm_lead_routes_to_lead_owner_fallback_then_end_fallback(self):
        source = self.inbound_source
        start = source.index("def resolve_lead_owner_inbound_target")
        end = source.index("def create_unknown_inbound_lead", start)
        owner_source = source[start:end]
        fallback_start = source.index("def _next_inbound_fallback")
        fallback_end = source.index("def _active_agent_mobile", fallback_start)
        fallback_source = source[fallback_start:fallback_end]

        self.assertIn('owner = (lead.get("lead_owner") or "").strip()', owner_source)
        self.assertIn('"route_type": "lead_owner"', owner_source)
        self.assertIn("_fallback_users(mapping)", owner_source)
        self.assertIn('"route_type": "lead_owner_fallback_user"', owner_source)
        self.assertIn("_end_fallback_mobile(settings)", owner_source)
        self.assertIn('"route_type": "lead_owner_end_fallback_mobile"', owner_source)
        self.assertIn('"skip_busy_callback_ai_fallback": True', owner_source)
        self.assertIn('"" if _request_flag(doc, "skip_busy_callback_ai_fallback") else _busy_callback_ai_fallback_mobile()', fallback_source)

    def test_unknown_route_uses_current_console_availability_contract(self):
        source = self.inbound_source

        self.assertIn("is_agent_console_online(user)", source)
        self.assertIn("mapping = get_user_mapping(user)", source)
        self.assertIn("unavailable_reason = get_mapping_unavailable_reason(mapping)", source)
        self.assertIn("_mark_mapping_busy(target.get(\"user\"), call_log.name)", source)

    def test_unknown_route_creates_lead_and_publishes_existing_panel_event(self):
        source = self.inbound_source

        self.assertIn("create_unknown_inbound_lead(customer_number, did_number, mapping, target)", source)
        self.assertIn('lead.status = defaults.get("status")', source)
        self.assertIn("lead.sr_lead_pipeline = defaults[\"pipeline\"]", source)
        self.assertIn("lead.sr_lead_platform = defaults[\"platform\"]", source)
        self.assertIn("assign_unknown_inbound_lead(lead.name, target.get(\"user\"))", source)
        self.assertIn("publish_callback_notification(call_log, None, customer_number, did_number, agent_mobile)", source)

    def test_unknown_lead_defaults_follow_vobiz_ai_defaulting(self):
        source = self.inbound_source
        start = source.index("def unknown_inbound_lead_defaults")
        end = source.index("def _first_doc", start)
        defaults_source = source[start:end]

        self.assertIn('"status": mapping.get("default_lead_status") or "Select Option"', defaults_source)
        self.assertIn('getattr(ai_settings, "default_pipeline", None) or _first_doc("SR Lead Pipeline")', defaults_source)
        self.assertIn('getattr(ai_settings, "default_platform", None) or _first_doc("SR Lead Platform")', defaults_source)

    def test_lead_creation_bypasses_field_guards_without_skipping_other_hooks(self):
        source = self.inbound_source
        start = source.index("def create_unknown_inbound_lead")
        end = source.index("def assign_unknown_inbound_lead", start)
        lead_source = source[start:end]

        self.assertIn('frappe.flags.sr_bypass_field_guard = True', lead_source)
        self.assertIn('lead.insert(ignore_permissions=True)', lead_source)
        self.assertIn('frappe.flags.sr_bypass_field_guard = previous_bypass', lead_source)
        self.assertNotIn('lead.flags.ignore_validate', lead_source)
        self.assertNotIn('lead.flags.ignore_links', lead_source)

    def test_lead_owner_assignment_avoids_team_hook_throw_path(self):
        source = self.inbound_source
        start = source.index("def assign_unknown_inbound_lead")
        end = source.index("def create_unknown_inbound_call_log", start)
        assignment_source = source[start:end]

        self.assertIn('_single_active_team_for_user(user)', assignment_source)
        self.assertIn('frappe.db.set_value("CRM Lead", lead_name, values, update_modified=False)', assignment_source)
        self.assertIn('return rows[0].parent if len(rows) == 1 else ""', assignment_source)

    def test_doctype_fields_match_incoming_mapping_contract(self):
        mapping = json.loads(INCOMING_MAPPING_JSON.read_text(encoding="utf-8"))
        agent = json.loads(INCOMING_AGENT_JSON.read_text(encoding="utf-8"))
        mapping_fields = {field["fieldname"]: field for field in mapping["fields"]}
        agent_fields = {field["fieldname"]: field for field in agent["fields"]}

        self.assertEqual(mapping["name"], "Vobiz Incoming Mapping")
        self.assertEqual(mapping["autoname"], "field:normalized_did")
        self.assertEqual(mapping_fields["routing_strategy"]["options"], "Round Robin\nLoad Balancing")
        self.assertEqual(mapping_fields["default_lead_status"]["default"], "Select Option")
        self.assertEqual(mapping_fields["agents"]["options"], "Vobiz Incoming Mapping Agent")

        self.assertEqual(agent["name"], "Vobiz Incoming Mapping Agent")
        self.assertEqual(agent["istable"], 1)
        self.assertEqual(agent_fields["agent_user"]["options"], "User")
        self.assertEqual(agent_fields["agent_mobile"]["options"], "Phone")

    def test_select_option_patch_is_registered(self):
        self.assertIn(
            "vobiz_click_to_call.patches.v1_0.add_select_option_crm_lead_status",
            PATCHES.read_text(encoding="utf-8"),
        )

    def test_inbound_busy_dial_result_keeps_busy_status(self):
        source = self.inbound_source
        start = source.index("def dial_action(")
        end = source.index("def find_last_customer_agent", start)
        dial_action_source = source[start:end]

        self.assertIn('status == "busy"', dial_action_source)
        self.assertIn('doc.status = "Busy"', dial_action_source)
        self.assertIn('doc.status = "No Answer"', dial_action_source)

    def test_missing_inbound_cdr_recovery_is_scheduled(self):
        hooks = HOOKS.read_text(encoding="utf-8")
        cdr = CDR_SERVICE.read_text(encoding="utf-8")

        self.assertIn("vobiz_click_to_call.services.cdr.enqueue_missing_inbound_cdr_sync", hooks)
        self.assertIn("def sync_missing_inbound_cdrs", cdr)
        self.assertIn("def create_missing_inbound_call_log_from_cdr", cdr)
        self.assertIn('"direction": "Incoming"', cdr)
        self.assertIn('status_from_cdr(cdr, "No Answer")', cdr)
        self.assertIn("def _normalize_cdr_phone", cdr)


if __name__ == "__main__":
    unittest.main()
