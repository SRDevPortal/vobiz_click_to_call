import sqlite3
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vobiz_click_to_call.api import console


class TestConsoleWhatsAppLive(TestCase):
    def setUp(self):
        window_patch = patch("wa_chat_hub.messaging.windows.get_messaging_window_state", return_value={"can_send_free_form": False})
        window_patch.start()
        self.addCleanup(window_patch.stop)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('''CREATE TABLE `tabChat Message` (
            name TEXT, conversation TEXT, creation TEXT, direction TEXT, sender_type TEXT,
            content_type TEXT, body TEXT, media_url TEXT, attachment_file TEXT, delivery_status TEXT, raw_transport_payload TEXT
        )''')
        self.frappe = MagicMock()
        self.frappe.session.user = "agent@example.com"
        self.frappe.utils.cint = int
        self.frappe.db.exists.return_value = True
        self.frappe.db.get_value.side_effect = self.get_cursor
        self.frappe.db.sql.side_effect = self.query
        self.frappe.throw.side_effect = ValueError("Cursor not found")
        patcher = patch.object(console, "frappe", self.frappe)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(console, "_existing_fields", side_effect=lambda meta, fields: list(fields))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(console, "_", side_effect=lambda message: message)
        patcher.start()
        self.addCleanup(patcher.stop)

    def insert(self, name, creation, conversation="CONV"):
        self.db.execute(
            'INSERT INTO `tabChat Message` (name, conversation, creation, content_type) VALUES (?, ?, ?, ?)',
            (name, conversation, creation, "Text"),
        )

    def get_cursor(self, doctype, filters, fields, **kwargs):
        if doctype == "Chat Conversation":
            return frappe._dict(unread_count=2, modified="2026-09-16 12:00:01")
        row = self.db.execute(
            'SELECT name, creation FROM `tabChat Message` WHERE name = ? AND conversation = ?',
            (filters["name"], filters["conversation"]),
        ).fetchone()
        return frappe._dict(dict(row)) if row else None

    def query(self, sql, params, **kwargs):
        return [frappe._dict(dict(row)) for row in self.db.execute(sql.replace("%s", "?"), params)]

    def test_timestamp_ties_and_multiple_pages_do_not_drop_or_repeat_messages(self):
        self.insert("A", "2026-09-16 12:00:00")
        self.insert("B", "2026-09-16 12:00:00")
        self.insert("C", "2026-09-16 12:00:00")
        self.insert("D", "2026-09-16 12:00:01")
        self.insert("X", "2026-09-16 12:00:00", "OTHER")
        page = console._whatsapp_messages_page("CONV", 2, after_message="A")
        self.assertEqual([row.name for row in page["messages"]], ["B", "C"])
        self.assertTrue(page["has_more_after"])
        page = console._whatsapp_messages_page("CONV", 2, after_message="C")
        self.assertEqual([row.name for row in page["messages"]], ["D"])
        self.assertFalse(page["has_more_after"])
        self.assertEqual(console._whatsapp_messages_page("CONV", 2, after_message="D")["messages"], [])

    def test_cursor_from_another_conversation_is_rejected(self):
        self.insert("OTHER-MESSAGE", "2026-09-16 12:00:00", "OTHER")
        with self.assertRaises(ValueError):
            console._whatsapp_messages_page("CONV", 30, after_message="OTHER-MESSAGE")
        self.frappe.db.sql.assert_not_called()

    def test_large_catchup_is_bounded_and_signals_more_pages(self):
        for index in range(105):
            self.insert(f"M{index:03}", "2026-09-16 12:00:00")
        page = console._whatsapp_messages_page("CONV", 10000, after_message="M000")
        self.assertEqual(len(page["messages"]), 100)
        self.assertEqual(page["messages"][-1].name, "M100")
        self.assertTrue(page["has_more_after"])

    def test_incremental_endpoint_uses_existing_console_access_check(self):
        self.insert("A", "2026-09-16 12:00:00")
        self.insert("B", "2026-09-16 12:00:01")
        with patch.object(console, "_ensure_whatsapp_conversation_read") as authorize:
            response = console.get_whatsapp_messages(
                "CONV", reference_doctype="Patient", reference_name="PAT", after_message="A"
            )
        authorize.assert_called_once_with("CONV", "Patient", "PAT")
        self.assertTrue(response["success"])
        self.assertEqual([row.name for row in response["messages"]], ["B"])

    def test_unauthorized_request_does_not_fetch_messages(self):
        with patch.object(console, "_ensure_whatsapp_conversation_read", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                console.get_whatsapp_messages("CONV", after_message="A")
        self.frappe.db.sql.assert_not_called()
        self.frappe.get_all.assert_not_called()
