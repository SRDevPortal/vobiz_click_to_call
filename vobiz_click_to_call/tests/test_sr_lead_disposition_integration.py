from __future__ import annotations

from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1]
LEAD_DISPOSITION = APP / "services" / "lead_disposition.py"
DISPOSITION = APP / "services" / "disposition.py"
DISPOSITION_API = APP / "api" / "disposition.py"
AI = APP / "services" / "ai.py"
SETTINGS = APP / "services" / "settings.py"
VOBIZ_SETTINGS = APP / "vobiz_click_to_call" / "doctype" / "vobiz_settings" / "vobiz_settings.py"
VOBIZ_SETTINGS_JSON = APP / "vobiz_click_to_call" / "doctype" / "vobiz_settings" / "vobiz_settings.json"
VOBIZ_SETTINGS_JS = APP / "public" / "js" / "vobiz_settings.js"
CONSOLE = APP / "api" / "console.py"
HOOKS = APP / "hooks.py"
INSTALL = APP / "install.py"


class TestSRLeadDispositionIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lead_disposition = LEAD_DISPOSITION.read_text(encoding="utf-8")
        cls.disposition = DISPOSITION.read_text(encoding="utf-8")
        cls.disposition_api = DISPOSITION_API.read_text(encoding="utf-8")
        cls.ai = AI.read_text(encoding="utf-8")
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.vobiz_settings = VOBIZ_SETTINGS.read_text(encoding="utf-8")
        cls.vobiz_settings_json = VOBIZ_SETTINGS_JSON.read_text(encoding="utf-8")
        cls.vobiz_settings_js = VOBIZ_SETTINGS_JS.read_text(encoding="utf-8")
        cls.console = CONSOLE.read_text(encoding="utf-8")
        cls.hooks = HOOKS.read_text(encoding="utf-8")
        cls.install = INSTALL.read_text(encoding="utf-8")

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
        self.assertNotIn("Call notes are required", self.disposition)
        self.assertIn('notes: str = ""', self.disposition_api)
        self.assertIn('notes: str = ""', self.disposition)
        self.assertIn("lead_sync", self.disposition)

    def test_vobiz_settings_ai_options_sync_from_sr_lead_disposition(self):
        self.assertIn("self.sync_ai_disposition_options()", self.vobiz_settings)
        self.assertIn("def get_sr_lead_disposition_options", self.vobiz_settings)
        self.assertIn("get_lead_disposition_rows()", self.vobiz_settings)
        self.assertIn("@frappe.whitelist()", self.vobiz_settings)
        self.assertIn("sync_ai_disposition_options", self.vobiz_settings_js)
        self.assertIn('"read_only": 1', self.vobiz_settings_json)
        self.assertIn("Synced from active SR Lead Disposition", self.vobiz_settings_json)
        self.assertIn('"Vobiz Settings": "public/js/vobiz_settings.js"', self.hooks)
        self.assertIn("validate_public_callback_base_url", self.vobiz_settings)

    def test_callback_urls_require_public_https_base_url(self):
        self.assertIn("def validate_public_callback_base_url", self.settings)
        self.assertIn('parsed.scheme != "https"', self.settings)
        self.assertIn("localhost", self.settings)
        self.assertIn("ip.is_private", self.settings)
        self.assertIn("Vobiz Webhook Base URL must be a public HTTPS URL", self.settings)
        self.assertIn('scheme == "http"', self.settings)
        self.assertIn('path.startswith(("/app", "/desk", "/login", "/api"))', self.settings)

    def test_callback_base_url_normalizes_full_desk_urls(self):
        import importlib.util

        module_path = SETTINGS
        spec = importlib.util.spec_from_file_location("vobiz_settings_service", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(
            module.normalize_public_callback_base_url("http://dev-sr.butest.tech/app/vobiz-agent-console"),
            "https://dev-sr.butest.tech",
        )
        self.assertEqual(
            module.normalize_public_callback_base_url("https://dev-sr.butest.tech/api/method/test"),
            "https://dev-sr.butest.tech",
        )

    def test_call_log_disposition_field_uses_sr_lead_disposition_selector(self):
        self.assertIn("ensure_vobiz_call_log_disposition_field()", self.install)
        self.assertIn('"Vobiz Call Log", "disposition", "fieldtype", "Select"', self.install)
        self.assertIn("get_vobiz_call_log_disposition_options", self.install)
        self.assertIn('"Vobiz Call Log", "disposition", "reqd", "0"', self.install)
        self.assertIn("sync_call_log_disposition_options(disposition)", self.disposition)

    def test_ai_uses_existing_sr_lead_dispositions(self):
        self.assertIn("get_lead_disposition_rows(doc.reference_doctype, doc.reference_name)", self.ai)
        self.assertIn("The disposition must be an existing SR Lead Disposition", self.ai)
        self.assertNotIn("frappe.get_doc({\"doctype\": \"SR Lead Disposition\"", self.ai)
        self.assertIn("sync_call_disposition_to_lead(doc, disposition)", self.ai)
        self.assertIn("doc.disposition = disposition", self.ai)
        self.assertIn("auto_disposed = bool(settings.enable_ai_disposition and not review_required)", self.ai)
        self.assertIn("lead_auto_applied = bool(settings.auto_apply_ai_disposition and auto_disposed)", self.ai)

    def test_ai_disposition_system_prompt_is_editable_in_settings(self):
        self.assertIn("DEFAULT_AI_DISPOSITION_SYSTEM_PROMPT", self.ai)
        self.assertIn("ai_disposition_system_prompt", self.ai)
        self.assertIn("build_prompt(doc, dispositions, disposition_rows, settings)", self.ai)
        self.assertIn("self.ai_disposition_system_prompt", self.vobiz_settings)
        self.assertIn('"fieldname": "ai_disposition_system_prompt"', self.vobiz_settings_json)
        self.assertIn('"label": "System Prompt"', self.vobiz_settings_json)
        self.assertIn("Return only valid JSON in this shape", self.ai)

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

    def test_vobiz_ai_provider_status_updates_are_normalized_to_terminal_states(self):
        self.assertIn("normalize_provider_update_status(target, values)", self.ai)
        self.assertIn("def normalize_provider_update_status", self.ai)
        self.assertIn('"hangup"', self.ai)
        self.assertIn('"Completed" if current_status == "Connected" else "Cancelled"', self.ai)


if __name__ == "__main__":
    unittest.main()
