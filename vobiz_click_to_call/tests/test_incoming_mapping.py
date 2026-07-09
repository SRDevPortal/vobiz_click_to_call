from __future__ import annotations

import json
from pathlib import Path
import unittest


APP_ROOT = Path(__file__).resolve().parents[1]
INBOUND_API = APP_ROOT / "api" / "inbound.py"
PATCHES = APP_ROOT / "patches.txt"
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


class TestIncomingMappingSource(unittest.TestCase):
    def setUp(self):
        self.inbound_source = INBOUND_API.read_text(encoding="utf-8")

    def test_unknown_route_is_only_called_after_previous_agent_lookup(self):
        previous_index = self.inbound_source.index("previous = find_last_customer_agent(customer_number)")
        unknown_index = self.inbound_source.index("routed = route_unknown_inbound(customer_number, did_number, payload, settings)")
        resolve_index = self.inbound_source.index("target = resolve_inbound_target(previous, settings)")

        self.assertLess(previous_index, unknown_index)
        self.assertLess(unknown_index, resolve_index)

    def test_unknown_route_keeps_existing_lead_and_patient_out(self):
        source = self.inbound_source

        self.assertIn("existing = find_existing_reference(customer_number)", source)
        self.assertIn("return None", source[source.index("def find_existing_reference"): source.index("def find_incoming_mapping")])
        self.assertIn('find_by_phone("Patient"', source)
        self.assertIn('find_by_phone("CRM Lead"', source)

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


if __name__ == "__main__":
    unittest.main()
