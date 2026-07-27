from __future__ import annotations

import unittest

from vobiz_click_to_call.services.numbers import normalize_phone_number


class TestPhoneNumberNormalization(unittest.TestCase):
    def test_indian_did_with_domestic_trunk_prefix(self):
        self.assertEqual(
            normalize_phone_number("07971442651", default_country_code="+91"),
            "+917971442651",
        )

    def test_explicit_international_number_is_unchanged(self):
        self.assertEqual(
            normalize_phone_number("+917971442651", default_country_code="+91"),
            "+917971442651",
        )
