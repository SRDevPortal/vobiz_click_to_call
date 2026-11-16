from unittest import TestCase
from unittest.mock import MagicMock, patch
import frappe
from vobiz_click_to_call.patches.v1_0 import add_ndr_outcomes


class NDRMigrationReplayTests(TestCase):
    def test_existing_final_tag_type_is_preserved(self):
        db = MagicMock()
        db.get_value.return_value = frappe._dict(fieldtype="Link", options="NDR Tag")
        with patch.object(frappe, "db", db), patch.object(frappe, "reload_doc"), \
             patch.object(add_ndr_outcomes, "create_custom_fields") as create:
            add_ndr_outcomes.execute()
        tag = create.call_args.args[0]["Patient Encounter"][0]
        self.assertEqual((tag["fieldtype"], tag["options"]), ("Link", "NDR Tag"))

    def test_first_install_keeps_original_patch_order(self):
        db = MagicMock()
        db.get_value.return_value = None
        with patch.object(frappe, "db", db), patch.object(frappe, "reload_doc"), \
             patch.object(add_ndr_outcomes, "create_custom_fields") as create:
            add_ndr_outcomes.execute()
        tag = create.call_args.args[0]["Patient Encounter"][0]
        self.assertEqual(tag["fieldtype"], "Data")
