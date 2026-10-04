import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import frappe
from privacy_shield.policy import Capabilities
from vobiz_click_to_call import number_privacy as privacy
from vobiz_click_to_call.api import call, console, inbound


class ConsoleNumberPrivacyTests(unittest.TestCase):
    def setUp(self):
        for item in [
            patch.object(frappe.local, "flags", frappe._dict(in_test=True), create=True),
            patch.object(frappe.local, "site", "test.local", create=True),
            patch.object(frappe, "session", frappe._dict(user="agent")),
            patch.object(frappe, "conf", {"privacy_shield_desk_enabled": True, "encryption_key": "test-only-secret"}),
            patch.object(frappe, "get_installed_apps", return_value=["privacy_shield"]),
            patch("privacy_shield.policy.current_capabilities", return_value=Capabilities()),
        ]:
            item.start()
            self.addCleanup(item.stop)
        self.row = {"doctype": "Patient", "name": "HLC-PAT-2026-43550", "phone": "2025550101",
                    "phone_field": "mobile", "title": "Caller 2025550101"}
        self.doc = SimpleNamespace(doctype="Patient", name=self.row["name"])

    def test_reference_projection_masks_response_and_preserves_source_and_id(self):
        original = deepcopy(self.row)
        result = privacy.project_reference(self.row)
        self.assertNotIn("2025550101", str(result))
        self.assertEqual(result["name"], self.row["name"])
        self.assertTrue(result["phone_field"].startswith(privacy.PREFIX))
        self.assertTrue(result["phone_masked"])
        self.assertEqual(self.row, original)

    def test_choice_resolves_original_through_shared_call_resolver(self):
        result = privacy.project_reference(self.row)
        with patch.object(call, "collect_phone_candidates", return_value=[("mobile", "2025550101")]), \
             patch.object(call, "collect_linked_customer_phone_candidates", return_value=[]):
            self.assertEqual(call.resolve_target_number(self.doc, result["phone_field"], None),
                             ("2025550101", "mobile"))
            with self.assertRaises(frappe.ValidationError):
                call.resolve_target_number(self.doc, result["phone_field"], result["phone"])

    def test_linked_customer_selection_preserves_exact_original(self):
        choice = privacy.phone_choice("Patient", self.doc.name, "2025550199")
        with patch.object(call, "collect_phone_candidates", return_value=[("mobile", "2025550101")]), \
             patch.object(call, "collect_linked_customer_phone_candidates",
                          return_value=[("customer.phone", "2025550199")]):
            self.assertEqual(call.resolve_target_number(self.doc, choice), ("2025550199", "customer.phone"))

    def test_choices_are_bound_to_user_record_site_and_current_number(self):
        choice = privacy.phone_choice("Patient", self.doc.name, "2025550101")
        candidates = [("mobile", "2025550101")]
        with patch.object(frappe, "session", frappe._dict(user="other")):
            with self.assertRaises(frappe.PermissionError):
                privacy.resolve_choice(self.doc, choice, candidates)
        with patch.object(frappe.local, "site", "other.local"):
            with self.assertRaises(frappe.PermissionError):
                privacy.resolve_choice(self.doc, choice, candidates)
        with self.assertRaises(frappe.PermissionError):
            privacy.resolve_choice(SimpleNamespace(doctype="Patient", name="OTHER"), choice, candidates)
        with self.assertRaises(frappe.PermissionError):
            privacy.resolve_choice(self.doc, choice, [("mobile", "2025550199")])
        with self.assertRaises(frappe.PermissionError):
            privacy.resolve_choice(self.doc, choice[:-1] + ("0" if choice[-1] != "0" else "1"), candidates)

    def test_missing_key_fails_without_disclosing_number(self):
        frappe.conf.pop("encryption_key")
        with self.assertRaises(frappe.ValidationError):
            privacy.project_reference(self.row)

    def test_patient_choices_preserve_labels_and_distinct_selections(self):
        source = [{"fieldname": "mobile", "label": "Mobile", "number": "2025550101"},
                  {"fieldname": "phone", "label": "Phone", "number": "2025550199"}]
        result = privacy.project_choices(self.doc, source)
        self.assertEqual([row["label"] for row in result], ["Mobile", "Phone"])
        self.assertNotEqual(result[0]["fieldname"], result[1]["fieldname"])
        self.assertNotIn("2025550101", str(result))
        self.assertNotIn("2025550199", str(result))
        self.assertEqual(source[0]["number"], "2025550101")

    def test_details_mask_phone_fields_but_preserve_link_identity_and_source(self):
        data = {"name": "CRM-LEAD-2026-687089", "fields": [
            {"fieldname": "mobile_no", "fieldtype": "Data", "value": "2025550101"},
            {"fieldname": "sr_source_patient", "fieldtype": "Link", "value": self.doc.name},
        ]}
        result = privacy.project_details(data)
        self.assertNotIn("2025550101", str(result))
        self.assertEqual(result["fields"][1], data["fields"][1])
        self.assertEqual(data["fields"][0]["value"], "2025550101")

    def test_disabled_and_full_view_preserve_original_contract(self):
        with patch.object(frappe, "conf", {"privacy_shield_desk_enabled": False}):
            self.assertIs(privacy.project_reference(self.row), self.row)
        with patch("privacy_shield.policy.current_capabilities", return_value=Capabilities(True)):
            self.assertIs(privacy.project_reference(self.row), self.row)
            self.assertEqual(privacy.display_number("2025550101"), "2025550101")

    def test_provider_start_response_masks_only_returned_number(self):
        result = call._call_start_result(SimpleNamespace(name="CALL-1", status="Queued"),
                                        "Agent First", "2025550101", "2025550199")
        self.assertNotIn("2025550101", str(result))
        self.assertEqual(result["call_log"], "CALL-1")
        self.assertEqual(result["call_flow"], "Agent First")

    def test_queue_endpoint_projects_after_internal_selection(self):
        static = {"queue_meta": {}, "dispositions": [], "ai_disposition_enabled": False}
        with patch.object(console, "_agent_context", return_value={}), \
             patch.object(console, "_agent_queue_source", return_value="Patient"), \
             patch.object(console, "_selected_queue_source", return_value="Patient"), \
             patch.object(console, "_queue_doctype_for_source", return_value="Patient"), \
             patch.object(console, "_get_console_static_context", return_value=static), \
             patch.object(console, "_lead_queue", return_value=[self.row]), \
             patch.object(console, "get_call_capability", return_value={}), \
             patch.object(console, "_active_call", return_value=None):
            result = console.get_agent_console_data()
        self.assertNotIn("2025550101", str(result["queue"]))
        self.assertEqual(self.row["phone"], "2025550101")

    def test_reference_endpoint_preserves_permission_denial(self):
        with patch.object(console, "_get_permitted_reference", side_effect=frappe.PermissionError), \
             patch.object(console, "_call_history") as history:
            with self.assertRaises(frappe.PermissionError):
                console.get_reference_context("Patient", self.doc.name)
            history.assert_not_called()

    def test_history_masks_customer_and_note_numbers_without_mutating_source(self):
        rows = [frappe._dict(name="CALL-2025550101", customer_number="2025550101",
                            ai_summary="Call +1 (202) 555-0199", ai_next_action=None,
                            transcript_text="Contact 2025550101 tomorrow", transcript_error="2025550101",
                            error_message="Destination 2025550101", hangup_cause="Failed 2025550101",
                            user_mobile="2025550188", recording_url=None, billsec=12)]
        original = deepcopy(rows)
        with patch.object(console.frappe, "get_all", return_value=rows):
            result = console._call_history("Patient", self.doc.name, 8)
        for field in ("customer_number", "ai_summary", "transcript_text",
                      "transcript_error", "error_message", "hangup_cause"):
            self.assertNotIn("2025550101", result[0][field])
            self.assertNotIn("555-0199", result[0][field])
        self.assertEqual(result[0]["name"], original[0]["name"])
        self.assertEqual(result[0]["user_mobile"], original[0]["user_mobile"])
        self.assertIsNone(result[0]["ai_next_action"])
        for key, value in original[0].items():
            self.assertEqual(rows[0][key], value)

    def test_missed_call_endpoint_masks_after_permission_and_preserves_identity(self):
        rows = [{"name": "CALL-1", "customer_number": "2025550101",
                 "error_message": "Failed for 2025550101", "reference_name": self.doc.name}]
        with patch.object(console, "_get_permitted_reference", return_value=self.doc), \
             patch.object(console, "_reference_missed_call_rows", return_value=rows):
            result = console.get_reference_missed_calls("Patient", self.doc.name)
        self.assertNotIn("2025550101", str(result))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["calls"][0]["reference_name"], self.doc.name)
        self.assertEqual(rows[0]["customer_number"], "2025550101")
        with patch.object(console, "_get_permitted_reference", side_effect=frappe.PermissionError), \
             patch.object(console, "_reference_missed_call_rows") as query:
            with self.assertRaises(frappe.PermissionError):
                console.get_reference_missed_calls("Patient", self.doc.name)
            query.assert_not_called()

    def test_history_disabled_or_full_view_keeps_original_payload(self):
        rows = [{"customer_number": "2025550101", "transcript_text": "2025550101"}]
        with patch.object(frappe, "conf", {"privacy_shield_desk_enabled": False}):
            self.assertIs(privacy.project_call_rows(rows), rows)
        with patch("privacy_shield.policy.current_capabilities", return_value=Capabilities(True)):
            self.assertIs(privacy.project_call_rows(rows), rows)
        self.assertEqual(privacy.project_call_rows([]), [])

    def test_appointment_defaults_omit_phone_without_masking_persisted_fields(self):
        defaults = {"Patient Appointment": {"patient": self.doc.name, "apt_mobile_number": "2025550101"},
                    "Patient Encounter": {"patient": self.doc.name, "sr_encounter_type": "Followup"},
                    "Sales Invoice": {"patient": self.doc.name, "customer": "CUST-1"}}
        original = deepcopy(defaults)
        result = privacy.project_create_defaults(defaults)
        self.assertEqual(result["Patient Appointment"], {"patient": self.doc.name})
        self.assertEqual(result["Patient Encounter"], defaults["Patient Encounter"])
        self.assertEqual(result["Sales Invoice"], defaults["Sales Invoice"])
        self.assertEqual(defaults, original)
        self.assertEqual(privacy.project_create_defaults({}), {})
        self.assertEqual(privacy.project_create_defaults({"Patient Appointment": {"patient": None,
                           "apt_mobile_number": "2025550101"}}),
                         {"Patient Appointment": {"patient": None}})
        with patch.object(frappe, "conf", {"privacy_shield_desk_enabled": False}):
            self.assertIs(privacy.project_create_defaults(defaults), defaults)
        with patch("privacy_shield.policy.current_capabilities", return_value=Capabilities(True)):
            self.assertIs(privacy.project_create_defaults(defaults), defaults)

    def test_workdesk_projects_creation_defaults_for_both_console_modes(self):
        from contextlib import ExitStack
        defaults = {"Patient Appointment": {"patient": self.doc.name, "apt_mobile_number": "2025550101"}}
        with ExitStack() as stack:
            for name, value in {
                "_resolve_patient": self.doc.name, "_agent_context": {}, "_lead_details": {},
                "get_lead_disposition_context": {}, "get_patient_followup_status_options_api": [],
                "_vobiz_summary": {}, "_whatsapp_deferred": {}, "_create_defaults": defaults,
                "_related_encounters": [], "_patient_clinical_history": {}, "_related_appointments": [],
                "_related_sales_invoices": [], "_related_reports": {}, "_whatsapp_preview": {},
            }.items():
                stack.enter_context(patch.object(console, name, return_value=value))
            for lite in (True, False):
                result = console._workdesk_context("Patient", self.doc.name, self.doc, lite=lite)
                self.assertEqual(result["create_defaults"]["Patient Appointment"], {"patient": self.doc.name})
        self.assertEqual(defaults["Patient Appointment"]["apt_mobile_number"], "2025550101")
    def test_callback_notification_projects_number_for_receiving_agent(self):
        call_log = frappe._dict(name="CALL-1", user="agent", reference_doctype="Patient",
                                reference_name=self.doc.name, crm_lead=None, patient=self.doc.name)
        with patch.object(privacy, "display_number", return_value="******0101") as display, \
             patch.object(frappe, "publish_realtime") as publish:
            inbound.publish_callback_notification(call_log, None, "2025550101", "18005550100", "2025550188")
        display.assert_called_once_with("2025550101", "agent")
        self.assertEqual(publish.call_args.args[1]["customer_number"], "******0101")
        self.assertEqual(publish.call_args.args[1]["did_number"], "18005550100")
        self.assertEqual(publish.call_args.kwargs, {"user": "agent", "after_commit": True})
