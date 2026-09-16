import json
from unittest import TestCase
from unittest.mock import patch

from vobiz_click_to_call.api import console
from wa_chat_hub.interakt import templates_api


class TestGuidedTemplateVariables(TestCase):
    def setUp(self):
        self.template = {
            "name": "appointment", "language_code": "en", "header_format": "TEXT",
            "header_variable_count": 1, "body_variable_count": 2,
        }
        for patcher in (
            patch.object(templates_api, "fetch_approved_templates", side_effect=lambda *a, **kw: [self.template]),
            patch.object(templates_api.frappe, "throw", side_effect=lambda message, *a, **kw: self.fail_validation(message)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def fail_validation(message):
        raise ValueError(message)

    def resolve(self, body, header):
        return templates_api.resolve_approved_template("ACCOUNT", {
            "template_name": "appointment", "language_code": "en",
            "body_values": console._list_from_template_values(body),
            "header_values": console._list_from_template_values(header),
        })

    def test_json_values_preserve_commas_zero_and_unicode(self):
        values = ["Sharma, Amit", "0"]
        result = self.resolve(json.dumps(values), '["दिल्ली"]')
        self.assertEqual(result["body_values"], values)
        self.assertEqual(result["header_values"], ["दिल्ली"])
        self.assertEqual(console._list_from_template_values([0, None, " last "]), ["0", "", "last"])

    def test_blank_body_position_is_not_removed_or_shifted(self):
        for values in (["", "Second"], '["First", " "]', [None, "Second"]):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, "Body variable.*required"):
                self.resolve(values, ["Header"])

    def test_missing_extra_and_blank_text_header_values_are_rejected(self):
        for header in ([], ["One", "Two"], [" "]):
            with self.subTest(header=header), self.assertRaises(ValueError):
                self.resolve(["First", "Second"], header)

    def test_wrong_body_count_is_rejected(self):
        for body in ([], ["One"], ["One", "Two", "Three"]):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "expects 2 body variables"):
                self.resolve(body, ["Header"])

    def test_image_header_keeps_approved_default_and_signed_url(self):
        self.template.update(header_format="IMAGE", header_variable_count=0, header_media_url="https://media.example/default.png")
        result = self.resolve(["First", "Second"], [])
        self.assertEqual(result["header_values"], ["https://media.example/default.png"])
        url = "https://media.example/image.png?signature=a,b"
        result = self.resolve(["First", "Second"], json.dumps([url]))
        self.assertEqual(result["header_values"], [url])

    def test_template_without_variables_accepts_empty_arrays(self):
        self.template.update(header_variable_count=0, body_variable_count=0)
        result = self.resolve([], [])
        self.assertEqual(result["body_values"], [])
        self.assertEqual(result["header_values"], [])
