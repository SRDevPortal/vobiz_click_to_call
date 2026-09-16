from contextlib import ExitStack
from io import BytesIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from PIL import Image
from werkzeug.datastructures import FileStorage
from vobiz_click_to_call.api import console
from wa_chat_hub.api import runtime


class TestConsoleAudioSticker(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.frappe = MagicMock()
        self.frappe.session.user = "agent@example.com"
        self.frappe.throw.side_effect = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
        self.stack.enter_context(patch.object(console, "frappe", self.frappe))
        self.stack.enter_context(patch.object(console, "_", lambda text: text))
        self.authorize = self.stack.enter_context(patch.object(console, "_ensure_whatsapp_conversation_read"))
        self.permission = self.stack.enter_context(patch("wa_chat_hub.messaging.windows.evaluate_send_permission"))

    def file(self, data=b"ID3audio", name="voice.mp3", mime="audio/mpeg"):
        file = FileStorage(stream=BytesIO(data), filename=name, content_type=mime)
        self.frappe.request.files = {"file": file}
        return file

    def webp(self, size=(512, 512), animated=False):
        output = BytesIO()
        image = Image.new("RGBA", size, "red")
        image.save(output, format="WEBP", save_all=animated,
                   append_images=[Image.new("RGBA", size, "blue")] if animated else [])
        return output.getvalue()

    def test_upload_authorizes_and_preserves_stream_for_shared_uploader(self):
        for kind, data, name, mime in [("audio", b"ID3audio", "voice.mp3", "audio/mp3"),
                                     ("sticker", self.webp(), "hello.webp", "image/webp")]:
            with self.subTest(kind=kind):
                file = self.file(data, name, mime)
                with patch.object(runtime, "_upload_authorized_media_for_send") as upload:
                    console.upload_whatsapp_media("CONV", kind, "Patient", "PATIENT")
                self.authorize.assert_called_with("CONV", "Patient", "PATIENT")
                self.permission.assert_called_with("CONV", kind.title())
                self.assertEqual(file.stream.read(), data)
                self.assertEqual(upload.call_args.args[1], {"audio/mpeg" if kind == "audio" else "image/webp"})

    def test_invalid_files_never_reach_provider(self):
        cases = [("audio", b"wave", "sound.wav", "audio/wav", "Choose MP3"),
                 ("audio", b"OggSvorbis", "sound.ogg", "audio/ogg", "Opus codec"),
                 ("audio", b"x" * (16 * 1024 * 1024 + 1), "big.mp3", "audio/mpeg", "16 MB"),
                 ("audio", b"", "empty.mp3", "audio/mpeg", "empty"),
                 ("sticker", b"fake", "fake.webp", "image/webp", "valid WebP"),
                 ("sticker", self.webp((32, 32)), "small.webp", "image/webp", "512"),
                 ("sticker", self.webp() + b"x" * (100 * 1024), "large.webp", "image/webp", "100 KB"),
                 ("sticker", b"x" * (500 * 1024 + 1), "huge.webp", "image/webp", "500 KB")]
        for kind, data, name, mime, error in cases:
            with self.subTest(name=name), patch.object(runtime, "_upload_authorized_media_for_send") as upload:
                self.file(data, name, mime)
                with self.assertRaisesRegex(ValueError, error):
                    console.upload_whatsapp_media("CONV", kind)
                upload.assert_not_called()

    def test_animated_sticker_and_ogg_opus_are_accepted(self):
        self.file(self.webp(animated=True) + b"x" * (100 * 1024), "animated.webp", "image/webp")
        self.assertEqual(console._validate_whatsapp_audio_or_sticker("sticker"), "image/webp")
        self.file(b"OggS" + b"x" * 24 + b"OpusHead", "voice.opus", "application/octet-stream")
        self.assertEqual(console._validate_whatsapp_audio_or_sticker("audio"), "audio/ogg")
        self.file(name="voice.m4a", mime="audio/x-m4a")
        self.assertEqual(console._validate_whatsapp_audio_or_sticker("audio"), "audio/mp4")

    def test_window_blocks_upload_and_queue_for_both_types(self):
        self.permission.return_value.ensure_allowed.side_effect = ValueError("Window closed")
        for kind in ("audio", "sticker"):
            with self.subTest(kind=kind), patch.object(runtime, "_upload_authorized_media_for_send") as upload, patch.object(runtime, "append_message") as append, patch.object(runtime, "task_log"):
                with self.assertRaisesRegex(ValueError, "Window closed"):
                    console.upload_whatsapp_media("CONV", kind)
                with self.assertRaisesRegex(ValueError, "Window closed"):
                    console.send_whatsapp_media("CONV", kind, "https://example.com/media")
                upload.assert_not_called()
                append.assert_not_called()

    def test_send_keeps_provider_local_urls_and_drops_unsupported_captions(self):
        for kind in ("Audio", "Sticker"):
            with self.subTest(kind=kind), patch.object(runtime, "_send_authorized_reply", return_value={"success": True}) as send:
                console.send_whatsapp_media("CONV", kind, "https://example.com/media", body="unsupported caption",
                                            display_media_url="/files/media", attachment_file="FILE",
                                            reference_doctype="Patient", reference_name="PATIENT")
                payload = send.call_args.args[0]
                self.assertEqual(payload["content_type"], kind)
                self.assertEqual(payload["body"], "")
                self.assertEqual(payload["media_url"], "https://example.com/media")
                self.assertEqual(payload["display_media_url"], "/files/media")
                self.assertEqual(payload["attachment_file"], "FILE")

    def test_access_denial_precedes_upload_validation_and_sending(self):
        self.authorize.side_effect = PermissionError
        for kind in ("audio", "sticker"):
            with self.subTest(kind=kind), patch.object(runtime, "_upload_authorized_media_for_send") as upload, patch.object(runtime, "_send_authorized_reply") as send:
                with self.assertRaises(PermissionError):
                    console.upload_whatsapp_media("OTHER", kind)
                with self.assertRaises(PermissionError):
                    console.send_whatsapp_media("OTHER", kind, "https://example.com/media")
                upload.assert_not_called()
                send.assert_not_called()

    def test_shared_upload_keeps_audio_mime_and_provider_url(self):
        file = self.file()
        account = MagicMock(channel_type="Interakt")
        account.get_password.return_value = "fake-test-key"
        self.frappe.get_doc.side_effect = [SimpleNamespace(channel_account="ACCOUNT"), account]
        response = MagicMock(ok=True, content=b"response")
        response.json.return_value = {"data": {"file_url": "https://example.com/audio"}}
        with patch.object(runtime, "frappe", self.frappe), patch.object(runtime.requests, "post", return_value=response) as post, patch.object(runtime, "save_file", return_value=SimpleNamespace(name="FILE", file_url="/files/voice.mp3", file_name="voice.mp3")), patch("wa_chat_hub.interakt.account_config.media_upload_api_url", return_value="https://example.com/upload"):
            result = console.upload_whatsapp_media("CONV", "audio")
        self.assertEqual(post.call_args.kwargs["files"]["uploadFile"][1:], (b"ID3audio", "audio/mpeg"))
        self.assertEqual(result["result"]["provider_file_url"], "https://example.com/audio")
        self.assertEqual(result["result"]["file_url"], "/files/voice.mp3")
