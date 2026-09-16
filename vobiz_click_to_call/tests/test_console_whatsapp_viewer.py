import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vobiz_click_to_call.api import console
from wa_chat_hub.api import chat


class TestConsoleMediaAccess(TestCase):
    def setUp(self):
        self.fake = MagicMock()
        self.fake.session.user = "agent@example.com"
        self.fake.utils.cint = lambda value: int(value or 0)
        self.fake.throw.side_effect = ValueError("Not allowed")
        self.row = frappe._dict(name=123, conversation="CONV", content_type="Image", media_url="https://media.example/image.png")
        self.fake.db.get_value.return_value = self.row
        self.authorize = MagicMock()
        self.serve = MagicMock()
        for patcher in (patch.object(console, "frappe", self.fake),
                        patch.object(console, "_", lambda value: value),
                        patch.object(console, "_ensure_whatsapp_conversation_read", self.authorize),
                        patch.object(chat, "_serve_authorized_message_media", self.serve)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_download_uses_stored_message_and_console_reference_authorization(self):
        console.get_whatsapp_message_media("123", download="1", reference_doctype="Patient", reference_name="PAT")
        self.authorize.assert_called_once_with("CONV", "Patient", "PAT")
        self.serve.assert_called_once_with(self.row, download=True)

    def test_preview_retains_inline_mode(self):
        console.get_whatsapp_message_media("123")
        self.serve.assert_called_once_with(self.row, download=False)

    def test_unauthorized_message_never_fetches_media(self):
        self.authorize.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            console.get_whatsapp_message_media("123", reference_doctype="Patient", reference_name="OTHER")
        self.serve.assert_not_called()

    def test_guest_and_missing_message_never_fetch_media(self):
        self.fake.session.user = "Guest"
        with self.assertRaises(ValueError):
            console.get_whatsapp_message_media("123")
        self.fake.db.get_value.assert_not_called()
        self.fake.session.user = "agent@example.com"
        self.fake.db.get_value.return_value = None
        with self.assertRaises(ValueError):
            console.get_whatsapp_message_media("missing")
        self.serve.assert_not_called()


class TestConsoleMediaMetadata(TestCase):
    def test_template_media_type_is_exposed_without_transport_payload(self):
        fake = MagicMock()
        fake.utils.cint = int
        fake.get_all.return_value = [frappe._dict(
            name=123, content_type="Template", creation="2026-09-16 12:00:00",
            raw_transport_payload=json.dumps({"header_format": "IMAGE", "header_media_url": "https://media.example/opaque"}),
        )]
        with patch.object(console, "frappe", fake), patch.object(console, "_existing_fields", side_effect=lambda meta, fields: list(fields)):
            result = console._whatsapp_messages_page("CONV", 30)
        row = result["messages"][0]
        self.assertEqual(row["media_url"], "https://media.example/opaque")
        self.assertEqual(row["media_content_type"], "Image")
        self.assertNotIn("raw_transport_payload", row)


class TestSharedMediaServing(TestCase):
    def test_preview_and_download_share_content_but_use_correct_disposition(self):
        for download, mime, expected in ((False, "image/png", "inline"), (True, "image/png", "attachment"), (False, "text/html", "attachment")):
            with self.subTest(download=download, mime=mime):
                fake = MagicMock()
                fake.response = {}
                response = MagicMock()
                response.headers = {"content-type": mime, "content-length": "3"}
                response.iter_content.return_value = [b"abc"]
                row = frappe._dict(content_type="Image", media_url="https://media.example/opaque")
                with patch.object(chat, "frappe", fake), patch.object(chat.requests, "get", return_value=response) as fetch:
                    chat._serve_authorized_message_media(row, download=download)
                fetch.assert_called_once_with(row.media_url, timeout=25, stream=True)
                self.assertEqual(fake.response["filecontent"], b"abc")
                self.assertEqual(fake.response["display_content_as"], expected)
                if mime == "image/png":
                    self.assertEqual(fake.response["filename"], "opaque.png")
