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
        self.assertNotIn("get_pipeline_map", patient_route)

    def test_crm_lead_pipeline_routing_remains_unchanged(self):
        start = self.source.index("\ndef _whatsapp_route_status_for_lead(")
        end = self.source.index("\ndef _interakt_account_for_outbound_pipeline(", start)

        self.assertIn("get_pipeline_map", self.source[start:end])

    def test_patient_access_and_new_conversation_use_department_routing(self):
        self.assertIn(
            'reference_doctype == "Patient" and not _has_mapped_patient_access',
            self.source,
        )
        self.assertIn("get_or_create_patient_conversation_for_channel_account", self.source)
        self.assertIn('route_status.get("channel_account")', self.source)


if __name__ == "__main__":
    unittest.main()
