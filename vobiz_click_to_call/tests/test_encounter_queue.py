import unittest
from unittest.mock import patch
import frappe
from vobiz_click_to_call.api import encounter_queue as queue


CATALOG = {name: dict(fieldname=name, label=name, fieldtype="Data")
           for name in ("name", "patient", "patient_name", "encounter_date", "status", "pe_shipkia_status")}


def reject(message, *args):
    raise ValueError(message)


class EncounterQueueTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"enable_encounter_queue": 1,
                         "encounter_queue_fields": '["name", "patient_name", "pe_shipkia_status"]',
                         "encounter_queue_filters": '[["pe_shipkia_status", "!=", "Delivered"]]'}
        self.catalog = patch.object(queue, "field_catalog", return_value=CATALOG)
        self.catalog.start()
        self.addCleanup(self.catalog.stop)
        self.throw = patch.object(frappe, "throw", side_effect=reject)
        self.throw.start()
        self.addCleanup(self.throw.stop)
        self.translate = patch.object(queue, "_", side_effect=lambda value: value)
        self.translate.start()
        self.addCleanup(self.translate.stop)

    def test_configuration_normalizes_filters_and_retains_column_order(self):
        columns, filters, _ = queue.configuration(self.settings)
        self.assertEqual(columns, ["name", "patient_name", "pe_shipkia_status"])
        self.assertEqual(filters, [["Patient Encounter", "pe_shipkia_status", "!=", "Delivered"]])

    def test_native_filter_group_hidden_flag_is_accepted(self):
        settings = dict(self.settings, encounter_queue_filters='[["Patient Encounter", "status", "=", "Open", false]]')
        self.assertEqual(queue.configuration(settings)[1], [["Patient Encounter", "status", "=", "Open"]])

    def test_invalid_configuration_is_rejected(self):
        for value in ('{}', 'invalid', '[["Other DocType", "status", "=", "Open"]]',
                      '[["name; DROP TABLE", "=", "x"]]', '[["name", "sql", "x"]]',
                      '[["name", [], "x"]]', '[["name", "is", "anything"]]'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                queue.configuration(dict(self.settings, encounter_queue_filters=value))
        with self.assertRaises(ValueError):
            queue.configuration(dict(self.settings, encounter_queue_fields='["password"]'))

    def test_pagination_and_saved_filters_use_permission_aware_query(self):
        rows = [{"name": str(i)} for i in range(3)]
        with patch.object(queue, "_settings", return_value=self.settings), \
             patch.object(queue, "get_permitted_fields", return_value=list(CATALOG)), \
             patch.object(frappe, "get_list", return_value=rows) as query:
            result = queue.get_queue.__wrapped__(limit=2, limit_start=4, search="PAT")
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(result["has_more"])
        args = query.call_args.kwargs
        self.assertEqual(args["limit_start"], 4)
        self.assertEqual(args["page_length"], 3)
        self.assertEqual(args["filters"], [["Patient Encounter", "pe_shipkia_status", "!=", "Delivered"]])
        self.assertEqual(args["or_filters"][0], ["Patient Encounter", "name", "like", "%PAT%"])
        self.assertNotIn("ignore_permissions", args)

    def test_unreadable_columns_are_omitted(self):
        settings = dict(self.settings, encounter_queue_filters="[]")
        with patch.object(queue, "_settings", return_value=settings), \
             patch.object(queue, "get_permitted_fields", return_value=["name"]), \
             patch.object(frappe, "get_list", return_value=[]) as query:
            result = queue.get_queue.__wrapped__()
        self.assertEqual([c["fieldname"] for c in result["columns"]], ["name"])
        self.assertEqual(query.call_args.kwargs["fields"], ["name"])

    def test_restricted_filter_is_rejected_before_query(self):
        with patch.object(queue, "_settings", return_value=self.settings), \
             patch.object(queue, "get_permitted_fields", return_value=["name"]), \
             patch.object(frappe, "get_list") as query, self.assertRaises(ValueError):
            queue.get_queue.__wrapped__()
        query.assert_not_called()

    def test_details_rechecks_saved_filters_and_record_visibility(self):
        with patch.object(queue, "_settings", return_value=self.settings), \
             patch.object(frappe, "get_list", return_value=[]) as query, \
             patch.object(frappe, "get_doc") as get_doc, self.assertRaises(ValueError):
            queue.get_encounter_context.__wrapped__("ENC-1")
        get_doc.assert_not_called()
        self.assertEqual(query.call_args.kwargs["filters"][-1],
                         ["Patient Encounter", "name", "=", "ENC-1"])


if __name__ == "__main__":
    unittest.main()
