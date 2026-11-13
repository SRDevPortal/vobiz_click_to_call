import unittest
from unittest.mock import patch, Mock
import frappe
from vobiz_click_to_call.services import queue_sources as access
from vobiz_click_to_call.api import console

class ConsoleTabAccessTests(unittest.TestCase):
    def test_mapping_matrix(self):
        for sources, expected in [
            ([], (False, False, False)),
            (["CRM Lead"], (True, False, False)),
            (["Patient"], (True, False, False)),
            (["Issue"], (True, False, False)),
            (["Discontinued"], (True, False, False)),
            (["Patient Encounter"], (False, True, False)),
            (["NDR"], (False, False, True)),
            (["Patient Encounter", "NDR"], (False, True, True)),
            (["Patient", "Patient Encounter", "NDR"], (True, True, True)),
        ]:
            with self.subTest(sources=sources), patch.object(access, "enabled_sources", return_value=sources), patch.object(frappe, "has_permission", return_value=True):
                self.assertEqual(tuple(access.available_views({"enable_encounter_queue": 1, "enable_ndr_queue": 1}).values()), expected)

    def test_settings_and_permissions(self):
        with patch.object(access, "enabled_sources", return_value=["Patient", "Patient Encounter", "NDR"]), patch.object(frappe, "has_permission", return_value=True):
            self.assertEqual(access.available_views({}), {"queue": True, "encounters": False, "ndr": False})
        with patch.object(access, "enabled_sources", return_value=["Patient", "Patient Encounter", "NDR"]), patch.object(frappe, "has_permission", return_value=False):
            self.assertFalse(any(access.available_views({"enable_encounter_queue": 1, "enable_ndr_queue": 1}).values()))

    def test_disabled_mapping_has_no_fallback(self):
        db = Mock()
        db.get_value.return_value = None
        with patch.object(frappe, "session", frappe._dict(user="agent@example.com")), patch.object(frappe, "db", db):
            self.assertEqual(access.enabled_sources(), [])
            db.get_value.assert_called_once_with("Vobiz User Mapping", {"user": "agent@example.com", "enabled": 1}, "queue_source")

    def test_hidden_sources_do_not_fetch_records(self):
        for source in ("Patient Encounter", "NDR", "CRM Lead"):
            with patch.object(console, "available_main_sources", return_value=["Patient"]), patch.object(console, "_queue_for_doctype") as query:
                self.assertEqual(console._lead_queue(25, queue_source=source), [])
                query.assert_not_called()
