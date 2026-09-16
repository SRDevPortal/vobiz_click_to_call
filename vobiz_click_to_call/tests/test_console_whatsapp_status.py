from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from vobiz_click_to_call.api import console


class TestConsoleWhatsAppStatus(TestCase):
    def setUp(self):
        window_patch = patch("wa_chat_hub.messaging.windows.get_messaging_window_state", return_value={"can_send_free_form": False})
        window_patch.start()
        self.addCleanup(window_patch.stop)
        permission_patch = patch("wa_chat_hub.messaging.windows.evaluate_send_permission")
        permission_patch.start()
        self.addCleanup(permission_patch.stop)
        self.frappe = MagicMock()
        self.frappe.session.user = "agent@example.com"
        self.frappe.throw.side_effect = ValueError("Invalid request")
        patcher = patch.object(console, "frappe", self.frappe)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(console, "_", side_effect=lambda value: value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_status_lookup_is_scoped_to_authorized_conversation_and_outbound_messages(self):
        self.frappe.get_all.return_value = [{"name": "MSG", "delivery_status": "Read"}]
        with patch.object(console, "_ensure_whatsapp_conversation_read") as authorize, patch.object(
            console, "_whatsapp_messages_page", return_value={"messages": []}
        ):
            result = console.get_whatsapp_messages(
                "CONV", reference_doctype="Patient", reference_name="PAT", after_message="MSG",
                status_message_names='["MSG", "MSG", "OTHER"]',
            )
        authorize.assert_called_once_with("CONV", "Patient", "PAT")
        self.frappe.get_all.assert_called_once_with(
            "Chat Message",
            filters={"conversation": "CONV", "direction": "Outbound", "name": ["in", ["MSG", "OTHER"]]},
            fields=["name", "delivery_status"], limit_page_length=100,
        )
        self.assertEqual(result["messages"], [])
        self.assertEqual(result["message_statuses"], [{"name": "MSG", "delivery_status": "Read"}])

    def test_unauthorized_status_lookup_never_queries_messages(self):
        with patch.object(console, "_ensure_whatsapp_conversation_read", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                console.get_whatsapp_messages("CONV", status_message_names=["MSG"])
        self.frappe.get_all.assert_not_called()

    def test_invalid_or_excessive_status_requests_are_rejected(self):
        for names in ('not json', '{}', [{}], [True], ['MSG'] * 101):
            with self.subTest(names=str(names)[:20]), self.assertRaises(ValueError):
                console._whatsapp_message_statuses("CONV", names)
        self.frappe.get_all.assert_not_called()

    def test_empty_status_request_does_not_add_a_query(self):
        for names in (None, [], '[]', ['']):
            self.assertEqual(console._whatsapp_message_statuses("CONV", names), [])
        self.frappe.get_all.assert_not_called()

    def test_numeric_message_ids_are_normalized_and_deduplicated(self):
        console._whatsapp_message_statuses("CONV", '[123,"123",456]')
        self.assertEqual(self.frappe.get_all.call_args.kwargs["filters"]["name"], ["in", ["123", "456"]])

    def test_provider_rejection_cannot_be_recorded_as_sent(self):
        self.frappe.get_doc.return_value = SimpleNamespace(channel_account="ACCOUNT", contact="CONTACT")
        with patch.object(console, "_ensure_whatsapp_conversation_read"), patch.object(
            console, "_whatsapp_messages_page", return_value={"messages": []}
        ), patch(
            "wa_chat_hub.outbound.send_outbound_message", return_value={"sent": False, "delivery_status": "Sent"}
        ), patch("wa_chat_hub.services.append_message", return_value={}) as append:
            console.send_whatsapp_reply("CONV", "Hello")
        self.assertEqual(append.call_args.args[0]["delivery_status"], "Failed")
