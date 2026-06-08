from __future__ import annotations

from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1]
LEAD_DISPOSITION = APP / "services" / "lead_disposition.py"
DISPOSITION = APP / "services" / "disposition.py"
AI = APP / "services" / "ai.py"
SETTINGS = APP / "services" / "settings.py"
CONSOLE = APP / "api" / "console.py"
HOOKS = APP / "hooks.py"


class TestSRLeadDispositionIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lead_disposition = LEAD_DISPOSITION.read_text(encoding="utf-8")
        cls.disposition = DISPOSITION.read_text(encoding="utf-8")
        cls.ai = AI.read_text(encoding="utf-8")
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.console = CONSOLE.read_text(encoding="utf-8")
        cls.hooks = HOOKS.read_text(encoding="utf-8")

    def test_sr_lead_disposition_is_source_of_truth(self):
        self.assertIn('SR_LEAD_DISPOSITION = "SR Lead Disposition"', self.lead_disposition)
        self.assertIn("get_lead_disposition_options", self.settings)
        self.assertIn("get_lead_disposition_options(reference_doctype, reference_name, lead_status)", self.settings)
        self.assertIn("get_lead_disposition_context", self.console)
        self.assertIn("get_lead_status_options", self.lead_disposition)
        self.assertIn("lead_status: str | None = None", self.lead_disposition)

    def test_manual_disposition_syncs_to_crm_lead(self):
        self.assertIn("sync_call_disposition_to_lead", self.disposition)
        self.assertIn("reference_doctype=doc.reference_doctype", self.disposition)
        self.assertIn("reference_name=doc.reference_name", self.disposition)
        self.assertIn("lead_status=lead_status", self.disposition)
        self.assertIn("lead_sync", self.disposition)

    def test_ai_uses_existing_sr_lead_dispositions(self):
        self.assertIn("get_lead_disposition_rows(doc.reference_doctype, doc.reference_name)", self.ai)
        self.assertIn("The disposition must be an existing SR Lead Disposition", self.ai)
        self.assertNotIn("frappe.get_doc({\"doctype\": \"SR Lead Disposition\"", self.ai)
        self.assertIn("sync_call_disposition_to_lead(doc, disposition)", self.ai)
        self.assertIn("doc.disposition = disposition", self.ai)
        self.assertIn("settings.auto_apply_ai_disposition", self.ai)

    def test_vobiz_ai_receive_transcript_can_trigger_click_to_call_ai(self):
        self.assertIn("on_update", self.hooks)
        self.assertIn("vobiz_click_to_call.services.ai.on_vobiz_call_log_update", self.hooks)
        self.assertIn("def maybe_enqueue_from_vobiz_ai_update", self.ai)
        self.assertIn('doc.get("transcription_text")', self.ai)
        self.assertIn("enqueue_ai_disposition(doc.name, commit=False)", self.ai)

    def test_vobiz_ai_provider_log_updates_restore_click_to_call_call(self):
        self.assertIn("def sync_provider_update_to_click_to_call_log", self.ai)
        self.assertIn('"source_app": "vobiz_click_to_call"', self.ai)
        self.assertIn("restore_mapping_for_call_log(target)", self.ai)
        self.assertIn("enqueue_ai_disposition(target, commit=False)", self.ai)
        self.assertIn('"transcription_text"', self.ai)


if __name__ == "__main__":
    unittest.main()
