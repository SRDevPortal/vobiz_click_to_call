from datetime import datetime
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vobiz_click_to_call.api import console
from wa_chat_hub import services


class TestConsoleWhatsAppRead(TestCase):
    def setUp(self):
        window_patch = patch("wa_chat_hub.messaging.windows.get_messaging_window_state", return_value={"can_send_free_form": False})
        window_patch.start()
        self.addCleanup(window_patch.stop)
        self.version = "2026-09-16 12:00:00.123456"
        self.frappe = MagicMock()
        self.frappe.session.user = "agent@example.com"
        self.frappe.utils.cint = lambda value: int(value or 0)
        self.frappe.utils.get_datetime = lambda value: datetime.fromisoformat(value) if isinstance(value, str) else value
        self.frappe.throw.side_effect = ValueError("Invalid request")
        self.frappe.db.get_value.return_value = frappe._dict(unread_count=3, modified=self.version)
        for target, name, value in (
            (console, "frappe", self.frappe),
            (console, "_", lambda value: value),
            (console, "_ensure_whatsapp_conversation_read", MagicMock()),
            (services, "mark_conversation_read", MagicMock()),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_matching_snapshot_marks_read_with_existing_console_authorization(self):
        result = console.mark_whatsapp_read("CONV", self.version, "Patient", "PAT")
        console._ensure_whatsapp_conversation_read.assert_called_once_with("CONV", "Patient", "PAT")
        self.frappe.db.get_value.assert_called_once_with(
            "Chat Conversation", "CONV", ["unread_count", "modified"], as_dict=True, for_update=True
        )
        services.mark_conversation_read.assert_called_once_with("CONV")
        self.assertTrue(result["marked_read"])
        self.assertEqual(result["unread_count"], 0)

    def test_message_arriving_after_snapshot_remains_unread(self):
        self.frappe.db.get_value.return_value.modified = "2026-09-16 12:00:01.123456"
        result = console.mark_whatsapp_read("CONV", self.version)
        self.assertFalse(result["marked_read"])
        self.assertEqual(result["unread_count"], 3)
        services.mark_conversation_read.assert_not_called()

    def test_unauthorized_request_never_reads_or_updates_state(self):
        console._ensure_whatsapp_conversation_read.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            console.mark_whatsapp_read("OTHER", self.version)
        self.frappe.db.get_value.assert_not_called()
        services.mark_conversation_read.assert_not_called()

    def test_guest_cannot_mark_read(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(ValueError):
            console.mark_whatsapp_read("CONV", self.version)
        console._ensure_whatsapp_conversation_read.assert_not_called()
        services.mark_conversation_read.assert_not_called()

    def test_missing_or_invalid_snapshot_cannot_clear_unread(self):
        for version in ("", "invalid"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                console.mark_whatsapp_read("CONV", version)
        services.mark_conversation_read.assert_not_called()

    def test_message_fetch_returns_snapshot_without_marking_read(self):
        with patch.object(console, "_whatsapp_messages_page", return_value={"messages": []}):
            result = console.get_whatsapp_messages("CONV")
        self.assertEqual(result["read_version"], self.version)
        self.assertEqual(result["unread_count"], 3)
        services.mark_conversation_read.assert_not_called()


class TestSharedReadService(TestCase):
    def test_locked_read_and_shared_event(self):
        fake = MagicMock()
        fake.db.get_value.return_value = 2
        fake.session.user = "agent@example.com"
        with patch.object(services, "frappe", fake), patch.object(services, "assert_ai_doctype_permission"), patch.object(
            services, "with_db_lock_retry", side_effect=lambda name, callback: callback()
        ):
            services.mark_conversation_read("CONV")
        fake.db.get_value.assert_called_once_with("Chat Conversation", "CONV", "unread_count", for_update=True)
        self.assertEqual(fake.db.sql.call_args.args[1], ("agent@example.com", "CONV"))
        fake.publish_realtime.assert_called_once_with(
            "wa_chat_conversation_updated", {"conversation": "CONV", "unread_count": 0}, after_commit=True
        )
