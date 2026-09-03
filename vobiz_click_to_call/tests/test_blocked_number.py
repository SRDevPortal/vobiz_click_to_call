from __future__ import annotations

import unittest
from unittest.mock import patch

from vobiz_click_to_call.vobiz_click_to_call.doctype.vobiz_blocked_number.vobiz_blocked_number import (
    VobizBlockedNumber,
)


class TestVobizBlockedNumber(unittest.TestCase):
    def test_before_naming_sets_normalized_phone_number(self):
        with patch(
            "vobiz_click_to_call.vobiz_click_to_call.doctype.vobiz_blocked_number"
            ".vobiz_blocked_number.get_default_country_code",
            return_value="+91",
        ):
            doc = object.__new__(VobizBlockedNumber)
            doc.phone_number = "9873090386"
            doc.normalized_phone_number = ""
            doc.before_naming()

        self.assertEqual(doc.normalized_phone_number, "+919873090386")


if __name__ == "__main__":
    unittest.main()
