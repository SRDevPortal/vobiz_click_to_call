from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import frappe
from vobiz_click_to_call.api import console
from wa_chat_hub import services


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

    def test_other_reference_types_keep_channel_scoped_lookup(self):
        start = self.source.index("\ndef _conversation_for_reference_phone(")
        end = self.source.index("\ndef _reference_phone_for_whatsapp(", start)
        lookup = self.source[start:end]

        self.assertIn('find_conversation_for_phone(phone, channel_account=channel_account)', lookup)

    def test_mapped_channel_can_create_lead_and_patient_conversations(self):
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


class TestExistingWhatsAppChatRouting(unittest.TestCase):
    def setUp(self):
        window_patch = patch("wa_chat_hub.messaging.windows.get_messaging_window_state", return_value={"can_send_free_form": False})
        window_patch.start()
        self.addCleanup(window_patch.stop)
        permission_patch = patch("wa_chat_hub.messaging.windows.evaluate_send_permission")
        permission_patch.start()
        self.addCleanup(permission_patch.stop)
        self.frappe = MagicMock()
        self.frappe.session.user = "agent@example.com"
        self.frappe.PermissionError = PermissionError
        self.frappe.throw.side_effect = lambda message, exc=ValueError: self._raise(exc, message)
        self.frappe.db.exists.return_value = True
        self.frappe.db.get_value.side_effect = lambda doctype, name, fields, **kw: (
            {"unread_count": 0, "modified": "2026-09-16 12:00:00"}
            if fields == ["unread_count", "modified"] else "EXISTING-ACCOUNT"
        )
        self.doc = MagicMock()
        self.frappe.get_doc.return_value = self.doc
        self.mapping = {"whatsapp_channel_account": "FALLBACK-ACCOUNT"}
        self.lookup = MagicMock(return_value="LATEST-CHAT")
        self.route = MagicMock(return_value={"available": True, "channel_account": "FALLBACK-ACCOUNT"})
        replacements = [
            (frappe, "local", SimpleNamespace(flags=SimpleNamespace(in_test=False))),
            (console, "frappe", self.frappe),
            (console, "_", lambda message: message),
            (console, "_agent_context", lambda: self.mapping),
            (console, "_has_mapped_patient_access", lambda *args: True),
            (console, "_reference_phone_for_whatsapp", lambda *args: "+919876543210"),
            (console, "_whatsapp_route_status", self.route),
            (console, "_existing_fields", lambda *args: []),
            (console, "_whatsapp_messages_page", MagicMock(return_value={"messages": []})),
            (services, "safe_ai_get_all", MagicMock(return_value=["CONTACT"])),
            (services, "safe_ai_get_value", self.lookup),
        ]
        for obj, name, value in replacements:
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _raise(exc, message):
        raise exc(message)

    def test_patient_and_lead_open_latest_updated_chat_across_accounts(self):
        for doctype in ("Patient", "CRM Lead"):
            with self.subTest(doctype=doctype):
                result = console.get_whatsapp_conversation(doctype, "REFERENCE")
                self.assertEqual(result, {"success": True, "conversation": "LATEST-CHAT"})
                self.lookup.assert_called_with(
                    "Chat Conversation", {"contact": ["in", ["CONTACT"]]}, "name",
                    order_by="modified desc, creation desc, name asc", for_update=False,
                )
                self.route.assert_not_called()

    def test_preview_uses_same_existing_chat_without_validating_fallback(self):
        with patch.object(console, "_mapped_agent_whatsapp_channel", side_effect=AssertionError("fallback used")):
            for doctype in ("Patient", "CRM Lead"):
                self.assertEqual(console._whatsapp_preview(doctype, "REFERENCE")["conversation"], "LATEST-CHAT")
                self.assertNotIn("channel_account", self.lookup.call_args.args[1])

    def test_no_existing_chat_creates_patient_or_lead_on_fallback_account(self):
        self.lookup.return_value = None
        for doctype, helper in (
            ("Patient", "get_or_create_patient_conversation_for_channel_account"),
            ("CRM Lead", "get_or_create_lead_conversation_for_channel_account"),
        ):
            with self.subTest(doctype=doctype), patch.object(console, "_resolve_patient", return_value="REFERENCE"), patch(
                f"wa_chat_hub.channel_resolver.{helper}", return_value={"conversation": "NEW-CHAT", "created": True},
            ) as create:
                result = console.get_whatsapp_conversation(doctype, "REFERENCE")
                self.assertEqual(result, {"success": True, "conversation": "NEW-CHAT", "created": True})
                create.assert_called_once_with(self.doc, "FALLBACK-ACCOUNT")

    def test_unmapped_user_still_reuses_existing_chat(self):
        self.mapping.clear()
        for doctype in ("Patient", "CRM Lead"):
            self.assertEqual(console.get_whatsapp_conversation(doctype, "REFERENCE")["conversation"], "LATEST-CHAT")
        self.route.assert_not_called()

    def test_unmapped_new_chat_keeps_resolved_department_account(self):
        self.mapping.clear()
        self.lookup.return_value = None
        self.route.return_value["channel_account"] = "DEPARTMENT-ACCOUNT"
        with patch("wa_chat_hub.channel_resolver.get_or_create_patient_conversation_for_channel_account",
                   return_value={"conversation": "NEW-CHAT", "created": True}) as create:
            console.get_whatsapp_conversation("Patient", "REFERENCE")
            create.assert_called_once_with(self.doc, "DEPARTMENT-ACCOUNT")

    def test_existing_customer_chat_can_be_read_across_accounts(self):
        for doctype in ("Patient", "CRM Lead"):
            self.assertEqual(console.get_whatsapp_messages("LATEST-CHAT", reference_doctype=doctype,
                                                         reference_name="REFERENCE")["messages"], [])
            self.lookup.assert_called_with(
                "Chat Conversation", {"contact": ["in", ["CONTACT"]], "name": "LATEST-CHAT"},
                "name", for_update=False,
            )

    def test_unrelated_chat_is_rejected_even_with_readable_reference(self):
        self.lookup.return_value = None
        with patch.object(services, "frappe", self.frappe), patch.object(services, "_", lambda message: message):
            with self.assertRaises(ValueError):
                console._ensure_whatsapp_conversation_read("OTHER-CUSTOMER", "Patient", "REFERENCE")

    def test_other_account_requires_reference_access(self):
        self.doc.has_permission.return_value = False
        with patch.object(console, "_has_mapped_patient_access", return_value=False):
            with self.assertRaises(PermissionError):
                console._ensure_whatsapp_conversation_read("LATEST-CHAT", "CRM Lead", "REFERENCE")
        self.lookup.assert_not_called()

    def test_other_account_without_reference_is_still_rejected(self):
        with self.assertRaises(PermissionError):
            console._ensure_whatsapp_conversation_read("LATEST-CHAT")

    def test_templates_are_loaded_from_existing_conversation_account(self):
        with patch("wa_chat_hub.api.runtime.get_interakt_templates", return_value={"result": {"templates": []}}) as templates:
            result = console.get_whatsapp_templates("LATEST-CHAT", reference_doctype="CRM Lead", reference_name="REFERENCE")
            self.assertTrue(result["success"])
            templates.assert_called_once_with(channel_account="EXISTING-ACCOUNT", force_refresh=0)

    def test_template_and_followup_stay_on_existing_chat(self):
        for doctype in ("Patient", "CRM Lead"):
            with self.subTest(doctype=doctype), patch(
                "wa_chat_hub.outbound.send_interakt_template_message", return_value={"sent": True},
            ) as template, patch(
                "wa_chat_hub.outbound.send_outbound_message", return_value={"sent": True},
            ) as reply, patch(
                "wa_chat_hub.interakt.templates_api.resolve_approved_template",
                side_effect=lambda account, payload: payload,
            ) as resolve_template, patch.object(services, "append_message", return_value={}) as append:
                result = console.send_whatsapp_template(
                    "LATEST-CHAT", "welcome", followup_body="Hello", reference_doctype=doctype, reference_name="REFERENCE",
                )
                self.assertTrue(result["success"])
                self.assertEqual(template.call_args.args[0], "LATEST-CHAT")
                self.assertEqual(resolve_template.call_args.args[0], "EXISTING-ACCOUNT")
                reply.assert_called_once_with("LATEST-CHAT", "Hello", "Text")
                self.assertEqual([call.args[0]["conversation"] for call in append.call_args_list],
                                 ["LATEST-CHAT", "LATEST-CHAT"])

    def test_other_wa_hub_callers_keep_original_conversation_order(self):
        services.find_conversation_for_phone("+919876543210", channel_account="ACCOUNT")
        self.assertEqual(self.lookup.call_args.kwargs["order_by"], "last_message_time desc, creation asc, name asc")
        self.assertEqual(self.lookup.call_args.args[1]["channel_account"], "ACCOUNT")


if __name__ == "__main__":
    unittest.main()
