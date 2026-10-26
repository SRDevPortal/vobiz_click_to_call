import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from vobiz_click_to_call.api import inbound


class TestInboundLeadNumbers(unittest.TestCase):
    def test_incoming_caller_is_saved_only_in_mobile(self):
        for number in ("9876543210", "+919876543210", "00919876543210"):
            with self.subTest(number=number):
                lead = SimpleNamespace(
                    name="TEST-INCOMING-LEAD",
                    meta=SimpleNamespace(fields=[
                        SimpleNamespace(fieldname=field)
                        for field in ("first_name", "lead_name", "mobile_no", "phone", "status")
                    ]),
                    mobile_no="",
                    phone="",
                    insert=Mock(),
                    reload=Mock(),
                )
                saved_numbers = []
                lead.insert.side_effect = lambda **kwargs: saved_numbers.append(
                    (lead.mobile_no, lead.phone)
                )
                flags = frappe._dict(sr_bypass_field_guard=False)
                with (
                    patch.object(inbound.frappe, "new_doc", return_value=lead),
                    patch.object(inbound.frappe, "flags", flags),
                    patch.object(inbound, "assign_unknown_inbound_lead") as assign,
                ):
                    result = inbound.create_unknown_inbound_lead(
                        number, "+911140000000", {}, {"user": "agent@example.test"}
                    )

                self.assertIs(result, lead)
                self.assertEqual(saved_numbers, [(number, "")])
                lead.insert.assert_called_once_with(ignore_permissions=True)
                assign.assert_called_once_with(lead.name, "agent@example.test")
                self.assertFalse(flags.sr_bypass_field_guard)
