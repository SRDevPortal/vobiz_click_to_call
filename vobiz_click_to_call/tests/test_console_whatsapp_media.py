from contextlib import ExitStack
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from vobiz_click_to_call.api import console
from wa_chat_hub.api import runtime


class TestConsoleWhatsAppMedia(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.frappe = MagicMock()
        self.frappe.session.user = "agent@example.com"
        self.frappe.PermissionError = PermissionError
        self.frappe.throw.side_effect = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
        self.frappe.get_doc.return_value = SimpleNamespace(channel_account="ACCOUNT", contact="CONTACT")
        self.frappe.db.get_value.return_value = "ACCOUNT"
        self.stack.enter_context(patch.object(console, "frappe", self.frappe))
        self.stack.enter_context(patch.object(console, "_", lambda text: text))
        self.authorize = self.stack.enter_context(patch.object(console, "_ensure_whatsapp_conversation_read"))
        self.stack.enter_context(patch.object(console, "_whatsapp_messages_page", return_value={}))

    def test_console_upload_uses_reference_authorization(self):
        with patch.object(runtime, "_upload_authorized_media_for_send", return_value={"success": True}) as upload:
            console.upload_whatsapp_media("CONV", "image", "Patient", "PATIENT")
        self.authorize.assert_called_once_with("CONV", "Patient", "PATIENT")
        self.assertEqual(upload.call_args.args[:2], ("CONV", {"image/"}))

    def test_unauthorized_upload_and_send_never_reach_transport(self):
        self.authorize.side_effect = PermissionError("Not permitted")
        with patch.object(runtime, "_upload_authorized_media_for_send") as upload, patch.object(
            runtime, "_send_authorized_reply"
        ) as send:
            with self.assertRaises(PermissionError):
                console.upload_whatsapp_media("OTHER")
            with self.assertRaises(PermissionError):
                console.send_whatsapp_media("OTHER", "Image", "https://media.example/image.png")
        upload.assert_not_called()
        send.assert_not_called()

    def test_media_send_preserves_provider_url_and_local_preview(self):
        with patch.object(runtime, "_send_authorized_reply", return_value={"success": True}) as send:
            console.send_whatsapp_media(
                "CONV", "Image", "https://media.example/image.png", display_media_url="/files/image.png",
                attachment_file="FILE", reference_doctype="Patient", reference_name="PATIENT",
            )
        self.authorize.assert_called_once_with("CONV", "Patient", "PATIENT")
        payload = send.call_args.args[0]
        self.assertEqual(payload["media_url"], "https://media.example/image.png")
        self.assertEqual(payload["display_media_url"], "/files/image.png")
        self.assertEqual(payload["attachment_file"], "FILE")
        self.assertEqual(payload["sender_type"], "Agent")

    def test_guest_cannot_upload_or_send(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(ValueError):
            console.upload_whatsapp_media("CONV")
        with self.assertRaises(ValueError):
            console.send_whatsapp_media("CONV", "Image", "https://media.example/image.png")
        self.authorize.assert_not_called()

    def _template_mocks(self):
        self.stack.enter_context(patch(
            "wa_chat_hub.interakt.templates_api.fetch_approved_templates",
            return_value=[{
                "name": "welcome_image", "language_code": "en", "body_variable_count": 0,
                "header_format": "IMAGE", "header_media_url": "https://media.example/approved.png",
            }],
        ))
        self.stack.enter_context(patch("wa_chat_hub.services.append_message", return_value={"message": "MSG"}))
        return self.stack.enter_context(patch(
            "wa_chat_hub.outbound.send_interakt_template_message",
            return_value={"sent": True, "delivery_status": "Sent"},
        ))

    def test_image_template_supplies_approved_header_automatically(self):
        send = self._template_mocks()
        result = console.send_whatsapp_template("CONV", "welcome_image")
        self.assertTrue(result["success"])
        self.assertEqual(send.call_args.args[1]["header_values"], ["https://media.example/approved.png"])

    def test_image_template_preserves_uploaded_replacement(self):
        send = self._template_mocks()
        console.send_whatsapp_template("CONV", "welcome_image", header_values=["https://media.example/new.png"])
        self.assertEqual(send.call_args.args[1]["header_values"], ["https://media.example/new.png"])

    def test_provider_failure_is_reported_and_followup_is_not_sent(self):
        send = self._template_mocks()
        send.side_effect = RuntimeError("Image URL rejected")
        with patch.object(console, "send_whatsapp_reply") as followup:
            result = console.send_whatsapp_template("CONV", "welcome_image", followup_body="Hello")
        self.assertFalse(result["success"])
        self.assertEqual(result["result"]["error"], "Image URL rejected")
        followup.assert_not_called()

    def test_template_success_is_preserved_when_window_blocks_optional_followup(self):
        send = self._template_mocks()
        with patch("wa_chat_hub.messaging.windows.evaluate_send_permission") as permission, patch(
            "wa_chat_hub.outbound.send_outbound_message"
        ) as send_text:
            permission.return_value.allowed = False
            result = console.send_whatsapp_template("CONV", "welcome_image", followup_body="Hello")
        self.assertTrue(result["success"])
        self.assertIn("Messaging window closed", result["followup_error"])
        send.assert_called_once()
        send_text.assert_not_called()
        self.frappe.db.commit.assert_called_once()


class TestSharedMediaReply(TestCase):
    def test_closed_window_still_blocks_console_images(self):
        with patch("wa_chat_hub.messaging.windows.evaluate_send_permission") as permission, patch.object(
            runtime, "append_message"
        ) as append, patch.object(runtime, "task_log"):
            permission.return_value.ensure_allowed.side_effect = ValueError("Messaging Window Closed")
            with self.assertRaisesRegex(ValueError, "Window Closed"):
                runtime._send_authorized_reply({
                    "conversation": "CONV", "content_type": "Image", "media_url": "https://media.example/a.png",
                })
        append.assert_not_called()

    def test_queue_uses_provider_url_but_message_uses_local_preview(self):
        mock_frappe = MagicMock()
        mock_frappe.get_doc.return_value = SimpleNamespace(channel_account="ACCOUNT", contact="CONTACT")
        with patch.object(runtime, "frappe", mock_frappe), patch.object(runtime, "task_log"), patch(
            "wa_chat_hub.messaging.windows.evaluate_send_permission"
        ), patch.object(runtime, "append_message", return_value={"message": "MSG"}) as append, patch.object(
            runtime, "_enqueue_pending_reply_send"
        ) as enqueue:
            result = runtime._send_authorized_reply({
                "conversation": "CONV", "content_type": "Image", "media_url": "https://media.example/a.png",
                "display_media_url": "/files/a.png", "attachment_file": "FILE",
            })
        self.assertTrue(result["result"]["queued"])
        self.assertEqual(append.call_args.args[0]["media_url"], "/files/a.png")
        self.assertEqual(enqueue.call_args.kwargs["media_url"], "https://media.example/a.png")

    def test_direct_hub_endpoints_still_require_hub_permission(self):
        mock_frappe = MagicMock()
        mock_frappe.local.form_dict = {"conversation": "CONV"}
        mock_frappe.form_dict = {"conversation": "CONV"}
        mock_frappe.request.get_json.return_value = None
        with patch.object(runtime, "frappe", mock_frappe), patch.object(
            runtime, "ensure_can_read_conversation", side_effect=PermissionError
        ), patch.object(runtime, "_upload_authorized_media_for_send") as upload, patch.object(
            runtime, "_send_authorized_reply"
        ) as send:
            with self.assertRaises(PermissionError):
                runtime.send_reply()
            with self.assertRaises(PermissionError):
                runtime.upload_image_for_send()
        upload.assert_not_called()
        send.assert_not_called()
