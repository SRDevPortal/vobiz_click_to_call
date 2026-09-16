from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vobiz_click_to_call.api import console
from wa_chat_hub.messaging import windows


class TestConsoleWhatsAppWindow(TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 16, 12)
        self.conversation = frappe._dict()
        self.frappe = MagicMock()
        self.frappe.session.user = "agent@example.com"
        self.frappe.utils.cint = lambda value: int(value or 0)
        self.frappe.db.get_value.return_value = {"unread_count": 0, "modified": self.now}
        self.authorize = MagicMock()
        for target, name, value in (
            (console, "frappe", self.frappe),
            (console, "_ensure_whatsapp_conversation_read", self.authorize),
            (console, "_whatsapp_messages_page", MagicMock(return_value={"messages": []})),
            (windows, "_messaging_window_fields_ready", lambda: True),
            (windows, "safe_ai_get_doc", MagicMock(side_effect=lambda *args: self.conversation)),
            (windows, "now_datetime", lambda: self.now),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fetch(self):
        return console.get_whatsapp_messages("CONV", reference_doctype="Patient", reference_name="PAT")["messaging_window"]

    def test_recent_reply_exposes_shared_expiry_and_server_time(self):
        self.conversation.last_customer_message_at = self.now - timedelta(hours=2)
        state = self.fetch()
        self.assertTrue(state["can_send_free_form"])
        self.assertEqual(state["free_form_expires_at"], str(self.now + timedelta(hours=22)))
        self.assertEqual(state["server_time"], str(self.now))
        self.authorize.assert_called_once_with("CONV", "Patient", "PAT")

    def test_initial_workdesk_preview_includes_window_without_incremental_fetch(self):
        self.conversation.last_customer_message_at = self.now - timedelta(hours=2)
        with patch.object(console, "_conversation_for_reference_phone", return_value="CONV"), patch.object(
            console, "_existing_fields", return_value=[]
        ):
            result = console._whatsapp_preview("Patient", "PAT")
        self.assertEqual(result["conversation"], "CONV")
        self.assertTrue(result["messaging_window"]["can_send_free_form"])
        self.assertEqual(result["messaging_window"]["free_form_expires_at"], str(self.now + timedelta(hours=22)))

    def test_exact_expiry_requires_template(self):
        self.conversation.last_customer_message_at = self.now - timedelta(hours=24)
        state = self.fetch()
        self.assertFalse(state["can_send_free_form"])
        self.assertTrue(state["can_send_template"])

    def test_first_contact_and_outbound_template_do_not_open_window(self):
        self.conversation.last_template_sent_at = self.now
        state = self.fetch()
        self.assertFalse(state["can_send_free_form"])
        self.assertIsNone(state["last_customer_message_at"])

    def test_verified_click_to_whatsapp_window_uses_shared_rules(self):
        self.conversation.update(last_customer_message_at=self.now - timedelta(hours=30),
                                 ctwa_clid="AD-CLICK", ctwa_window_expires_at=self.now + timedelta(hours=42))
        state = self.fetch()
        self.assertTrue(state["can_send_free_form"])
        self.assertEqual(state["reason"], "ctwa_72h")
        self.conversation.ctwa_clid = None
        self.assertFalse(self.fetch()["can_send_free_form"])

    def test_unauthorized_chat_cannot_disclose_window(self):
        self.authorize.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.fetch()
        windows.safe_ai_get_doc.assert_not_called()

    def test_closed_window_rejected_before_transport_or_outbound_record(self):
        decision = MagicMock()
        decision.allowed = False
        with patch.object(windows, "evaluate_send_permission", return_value=decision) as permission, patch(
            "wa_chat_hub.outbound.send_outbound_message"
        ) as send, patch("wa_chat_hub.services.append_message") as append:
            result = console.send_whatsapp_reply("CONV", "Draft", "Patient", "PAT")
        permission.assert_called_once_with("CONV", "Text")
        self.assertFalse(result["success"])
        self.assertIn("Messaging window closed", result["result"]["error"])
        send.assert_not_called()
        append.assert_not_called()
