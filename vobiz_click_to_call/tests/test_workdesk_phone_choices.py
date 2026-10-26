import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from vobiz_click_to_call.api import call
from vobiz_click_to_call import number_privacy


class TestWorkdeskPhoneChoices(unittest.TestCase):
    def setUp(self):
        translation = patch.object(call, "_", side_effect=lambda text: text)
        translation.start()
        self.addCleanup(translation.stop)

    def doc(self, **values):
        return SimpleNamespace(
            doctype="CRM Lead", name="TEST-LEAD", get=values.get,
            meta=SimpleNamespace(has_field=lambda field: field in values),
        )

    def test_available_fields_and_normalized_duplicates(self):
        cases = [
            ({"mobile_no": "9876543210", "phone": "9876543211"}, ["mobile_no", "phone"]),
            ({"mobile_no": "9876543210", "phone": ""}, ["mobile_no"]),
            ({"mobile_no": "", "phone": "9876543211"}, ["phone"]),
            ({"mobile_no": "9876543210", "phone": "+919876543210"}, ["mobile_no"]),
            ({"mobile_no": "", "phone": ""}, []),
        ]
        for values, expected in cases:
            with self.subTest(values=values):
                choices = call._primary_phone_candidates(self.doc(**values), "91", "mobile_no")
                self.assertEqual([c["fieldname"] for c in choices], expected)

    def test_masked_choices_resolve_to_selected_original_field(self):
        values = {"mobile_no": "9876543210", "phone": "9876543211"}
        doc = self.doc(**values)
        with (
            patch.object(call, "get_settings", return_value=None),
            patch.object(call, "get_default_country_code", return_value="91"),
            patch.object(number_privacy, "restricted", return_value=True),
            patch.object(frappe, "conf", {"encryption_key": "test-only-key"}),
            patch.object(frappe, "session", frappe._dict(user="agent")),
            patch.object(frappe.local, "site", "test.local", create=True),
        ):
            choices = call.get_workdesk_phone_choices(doc)
            self.assertNotIn(values["mobile_no"], str(choices))
            self.assertNotIn(values["phone"], str(choices))
            self.assertNotEqual(choices[0]["fieldname"], choices[1]["fieldname"])
            with (
                patch.object(call, "collect_phone_candidates", return_value=list(values.items())),
                patch.object(call, "collect_linked_customer_phone_candidates", return_value=[]),
            ):
                self.assertEqual(call.resolve_target_number(doc, choices[1]["fieldname"]), (values["phone"], "phone"))

    def test_unmasked_choices_keep_field_identifiers(self):
        doc = self.doc(mobile_no="9876543210", phone="9876543211")
        with (
            patch.object(call, "get_settings", return_value=None),
            patch.object(call, "get_default_country_code", return_value="91"),
            patch.object(number_privacy, "restricted", return_value=False),
        ):
            self.assertEqual([c["fieldname"] for c in call.get_workdesk_phone_choices(doc)], ["mobile_no", "phone"])
