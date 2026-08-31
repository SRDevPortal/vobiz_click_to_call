from __future__ import annotations

from pathlib import Path
import unittest


CONSOLE_API = Path(__file__).resolve().parents[1] / "api" / "console.py"


class TestPatientChatChannelRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CONSOLE_API.read_text(encoding="utf-8")

    def test_patient_chat_uses_channel_default_department_without_pipeline_map(self):
        start = self.source.index("\ndef _whatsapp_route_status_for_patient(")
        end = self.source.index("\ndef _whatsapp_recent_messages(", start)
        patient_route = self.source[start:end]

        self.assertIn('"default_medical_department": department', patient_route)
        self.assertIn('"channel_type": "Interakt"', patient_route)
        self.assertIn('"is_active": 1', patient_route)
        self.assertIn('account_filters["enable_patient_department_routing"] = 1', patient_route)
        self.assertNotIn("get_pipeline_map", patient_route)

    def test_crm_lead_pipeline_routing_remains_unchanged(self):
        start = self.source.index("\ndef _whatsapp_route_status_for_lead(")
        end = self.source.index("\ndef _interakt_account_for_outbound_pipeline(", start)

        self.assertIn("get_pipeline_map", self.source[start:end])

    def test_patient_access_and_new_conversation_use_department_routing(self):
        self.assertIn(
            'and not _has_mapped_patient_access(reference_doctype, reference_name)',
            self.source,
        )
        self.assertIn("get_or_create_patient_conversation_for_channel_account", self.source)
        self.assertIn('route_status.get("channel_account")', self.source)

    def test_user_mapping_channel_overrides_lead_and_patient_routing(self):
        start = self.source.index("\ndef _whatsapp_route_status(")
        end = self.source.index("\ndef _whatsapp_route_status_for_lead(", start)
        route = self.source[start:end]

        self.assertIn("_mapped_agent_whatsapp_channel()", route)
        self.assertIn('"routing_source": "Vobiz User Mapping"', route)

    def test_existing_conversation_lookup_is_scoped_to_mapped_channel(self):
        start = self.source.index("\ndef _conversation_for_reference_phone(")
        end = self.source.index("\ndef _reference_phone_for_whatsapp(", start)
        lookup = self.source[start:end]

        self.assertIn('conversation_filters["channel_account"] = channel_account', lookup)

    def test_missing_user_channel_preserves_existing_fallback(self):
        start = self.source.index("\ndef get_whatsapp_conversation(")
        end = self.source.index("\ndef get_whatsapp_messages(", start)
        method = self.source[start:end]

        self.assertIn("if not mapped_channel:", method)
        self.assertIn("_conversation_for_reference_phone(reference_doctype, reference_name)", method)

    def test_mapped_channel_creates_separate_lead_and_patient_conversations(self):
        start = self.source.index("\ndef get_whatsapp_conversation(")
        end = self.source.index("\ndef get_whatsapp_messages(", start)
        method = self.source[start:end]

        self.assertIn("get_or_create_lead_conversation_for_channel_account", method)
        self.assertIn("get_or_create_patient_conversation_for_channel_account", method)
        self.assertIn("channel_account=channel_account", method)

    def test_mapped_channel_bypasses_department_gate(self):
        start = self.source.index("\ndef get_whatsapp_conversation(")
        end = self.source.index("\ndef get_whatsapp_messages(", start)
        method = self.source[start:end]

        self.assertIn("not mapped_channel", method)
        self.assertIn("not _has_mapped_patient_access", method)

    def test_workdesk_access_is_scoped_to_mapped_channel(self):
        start = self.source.index("\ndef _ensure_whatsapp_conversation_read(")
        end = self.source.index("\ndef _create_defaults(", start)
        access = self.source[start:end]

        self.assertIn("conversation_channel == mapped_channel", access)
        self.assertIn("different Channel Account", access)
        self.assertIn("ensure_can_read_conversation(conversation)", access)

    def test_template_lookup_uses_already_authorized_channel(self):
        start = self.source.index("\ndef get_whatsapp_templates(")
        end = self.source.index("\ndef send_whatsapp_template(", start)
        templates = self.source[start:end]

        self.assertIn("get_interakt_templates(channel_account=channel_account", templates)
        self.assertNotIn("get_interakt_templates(conversation=conversation", templates)

    def test_workdesk_preview_is_scoped_to_mapped_channel(self):
        start = self.source.index("\ndef _whatsapp_preview(")
        end = self.source.index("\ndef _whatsapp_route_status(", start)
        preview = self.source[start:end]

        self.assertIn("mapped_channel = _mapped_agent_whatsapp_channel()", preview)
        self.assertIn("channel_account=mapped_channel", preview)


if __name__ == "__main__":
    unittest.main()
