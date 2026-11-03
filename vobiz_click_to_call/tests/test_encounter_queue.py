import unittest
from unittest.mock import Mock, patch
import frappe
from vobiz_click_to_call.api import encounter_queue as queue


CATALOG = {name: dict(fieldname=name, label=name, fieldtype="Data")
           for name in ("name", "patient", "patient_name", "encounter_date", "status", "pe_shipkia_stage", "pe_shipkia_status")}


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
        self.permitted = patch.object(queue, "get_permitted_fields", return_value=list(CATALOG))
        self.permitted.start()
        self.addCleanup(self.permitted.stop)

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

    def ndr_settings(self, **kwargs):
        return dict(self.settings, enable_ndr_queue=1,
                    ndr_shipkia_stages='["NDR", "In Transit"]',
                    ndr_shipkia_statuses='["Undelivered", "Reattempt"]', **kwargs)

    def test_ndr_inherits_filters_and_inserts_columns_without_duplicates(self):
        columns, filters, _ = queue.configuration(self.ndr_settings(), "ndr")
        self.assertEqual(columns, ["name", "patient_name", "pe_shipkia_stage", "pe_shipkia_status"])
        self.assertEqual(filters, [
            ["Patient Encounter", "pe_shipkia_status", "!=", "Delivered"],
            ["Patient Encounter", "pe_shipkia_stage", "in", ["NDR", "In Transit"]],
            ["Patient Encounter", "pe_shipkia_status", "in", ["Undelivered", "Reattempt"]],
        ])
        self.assertEqual(queue.configuration(self.ndr_settings())[0], ["name", "patient_name", "pe_shipkia_status"])

    def test_ndr_short_column_configuration_keeps_stage_status_third_fourth(self):
        columns, _, _ = queue.configuration(self.ndr_settings(encounter_queue_fields='["pe_shipkia_status"]'), "ndr")
        self.assertEqual(columns, ["name", "patient", "pe_shipkia_stage", "pe_shipkia_status"])

    def test_ndr_requires_both_multiselects_and_shipkia_fields(self):
        for setting in ("ndr_shipkia_stages", "ndr_shipkia_statuses"):
            for invalid in ('[]', '[""]', '["   "]', '[1]', '{}', 'invalid', '[["nested"]]'):
                settings = self.ndr_settings()
                settings[setting] = invalid
                with self.subTest(setting=setting, invalid=invalid), self.assertRaises(ValueError):
                    queue.configuration(settings, "ndr")
        with patch.object(queue, "field_catalog", return_value={k: v for k, v in CATALOG.items() if k != "pe_shipkia_stage"}), self.assertRaises(ValueError):
            queue.configuration(self.ndr_settings(), "ndr")

    def test_ndr_validates_even_when_encounter_tab_is_disabled(self):
        settings = self.ndr_settings()
        settings.update(enable_encounter_queue=0, ndr_shipkia_stages="[]")
        with patch.object(frappe, "db", Mock(exists=Mock(return_value=True))), self.assertRaises(ValueError):
            queue.validate_configuration(settings)

    def test_ndr_server_side_sort_preserves_pagination_search_and_permissions(self):
        with patch.object(queue, "_settings", return_value=self.ndr_settings()), \
             patch.object(frappe, "get_list", return_value=[{"name": "ENC-1"}, {"name": "ENC-2"}]) as query:
            result = queue.get_queue.__wrapped__(view="ndr", sort_by="pe_shipkia_status", sort_order="desc", limit=1, limit_start=25, search="PAT")
        args = query.call_args.kwargs
        self.assertEqual(args["order_by"], "pe_shipkia_status desc, modified desc, name desc")
        self.assertEqual(args["limit_start"], 25)
        self.assertEqual(args["page_length"], 2)
        self.assertEqual(len(args["filters"]), 3)
        self.assertEqual(len(args["or_filters"]), 3)
        self.assertTrue(result["has_more"])
        self.assertNotIn("ignore_permissions", args)

    def test_ndr_rejects_unapproved_sort_fields_and_directions(self):
        for view, field, direction in (("ndr", "name", "asc"), ("ndr", "pe_shipkia_stage", "desc; select 1"),
                                       ("encounters", "pe_shipkia_status", "asc"), ("unknown", "", "asc")):
            with self.subTest(view=view, field=field), patch.object(queue, "_settings", return_value=self.ndr_settings()), \
                 patch.object(frappe, "get_list") as query, self.assertRaises(ValueError):
                queue.get_queue.__wrapped__(view=view, sort_by=field, sort_order=direction)
            query.assert_not_called()

    def test_ndr_details_recheck_membership_and_filter_permissions(self):
        with patch.object(queue, "_settings", return_value=self.ndr_settings()), \
             patch.object(frappe, "get_list", return_value=[]) as query, \
             patch.object(frappe, "get_doc") as doc, self.assertRaises(ValueError):
            queue.get_encounter_context.__wrapped__("ENC-1", view="ndr")
        self.assertEqual(len(query.call_args.kwargs["filters"]), 4)
        self.assertEqual(query.call_args.kwargs["filters"][-2][1], "pe_shipkia_status")
        doc.assert_not_called()
        with patch.object(queue, "_settings", return_value=self.ndr_settings()), \
             patch.object(queue, "get_permitted_fields", return_value=["name", "pe_shipkia_status"]), \
             patch.object(frappe, "get_list") as query, self.assertRaises(ValueError):
            queue.get_encounter_context.__wrapped__("ENC-1", view="ndr")
        query.assert_not_called()

    def test_ndr_list_rejects_unreadable_stage(self):
        with patch.object(queue, "_settings", return_value=self.ndr_settings()), \
             patch.object(queue, "get_permitted_fields", return_value=["name", "pe_shipkia_status"]), \
             patch.object(frappe, "get_list") as query, self.assertRaises(ValueError):
            queue.get_queue.__wrapped__(view="ndr")
        query.assert_not_called()

    def test_ndr_disabled_cannot_be_requested_directly(self):
        with patch.object(frappe, "session", frappe._dict(user="agent@example.com")), \
             patch.object(frappe, "get_single", return_value=self.settings), self.assertRaises(ValueError):
            queue._settings("ndr")

    def test_ndr_visibility_requires_enabled_setting_mapping_and_read_permission(self):
        for enabled, mapped, readable, expected in (
            (1, True, True, True), (0, True, True, False),
            (1, False, True, False), (1, True, False, False),
        ):
            settings = self.ndr_settings()
            settings["enable_ndr_queue"] = enabled
            with self.subTest(enabled=enabled, mapped=mapped, readable=readable), \
                 patch.object(frappe, "session", frappe._dict(user="Administrator")), \
                 patch.object(frappe, "get_single", return_value=settings), \
                 patch.object(queue, "has_ndr_mapping", return_value=mapped), \
                 patch.object(frappe, "has_permission", return_value=readable):
                self.assertEqual(queue.get_available_views.__wrapped__(), {"ndr": expected})

    def test_ndr_mapping_is_read_fresh_for_current_enabled_user(self):
        db = Mock()
        db.get_value.side_effect = ["Patient\nNDR", "Patient", None]
        with patch.object(frappe, "session", frappe._dict(user="agent@example.com")), \
             patch.object(frappe, "db", db):
            self.assertTrue(queue.has_ndr_mapping())
            self.assertFalse(queue.has_ndr_mapping())
            self.assertFalse(queue.has_ndr_mapping())
        for call in db.get_value.call_args_list:
            self.assertEqual(call.args, ("Vobiz User Mapping", {"user": "agent@example.com", "enabled": 1}, "queue_source"))

    def test_ndr_list_and_details_reject_unmapped_users_before_reading_records(self):
        for user in ("agent@example.com", "Administrator"):
            with self.subTest(user=user), patch.object(frappe, "session", frappe._dict(user=user)), \
                 patch.object(frappe, "get_single", return_value=self.ndr_settings()), \
                 patch.object(queue, "has_ndr_mapping", return_value=False), \
                 patch.object(frappe, "get_list") as query, patch.object(frappe, "get_doc") as get_doc:
                with self.assertRaises(ValueError):
                    queue.get_queue.__wrapped__(view="ndr")
                with self.assertRaises(ValueError):
                    queue.get_encounter_context.__wrapped__("ENC-1", view="ndr")
                query.assert_not_called()
                get_doc.assert_not_called()

    def test_mapped_ndr_access_still_requires_encounter_permission(self):
        with patch.object(frappe, "session", frappe._dict(user="agent@example.com")), \
             patch.object(frappe, "get_single", return_value=self.ndr_settings()), \
             patch.object(queue, "has_ndr_mapping", return_value=True), \
             patch.object(frappe, "has_permission", return_value=False), self.assertRaises(ValueError):
            queue._settings("ndr")

    def test_encounter_list_does_not_require_ndr_mapping(self):
        with patch.object(frappe, "session", frappe._dict(user="agent@example.com")), \
             patch.object(frappe, "get_single", return_value=self.settings), \
             patch.object(queue, "has_ndr_mapping") as mapped, \
             patch.object(frappe, "has_permission", return_value=True):
            self.assertEqual(queue._settings("encounters"), self.settings)
        mapped.assert_not_called()

    def test_ndr_options_are_manager_only_and_permission_aware(self):
        with patch.object(frappe, "get_roles", return_value=["System Manager"]), \
             patch.object(frappe, "get_list", return_value=[{"pe_shipkia_stage": "NDR"}]) as query:
            self.assertEqual(queue.get_ndr_options.__wrapped__("pe_shipkia_stage", "ND"), ["NDR"])
        self.assertEqual(query.call_args.kwargs["group_by"], "pe_shipkia_stage")
        self.assertEqual(query.call_args.kwargs["filters"][-1][-1], "%ND%")
        self.assertNotIn("ignore_permissions", query.call_args.kwargs)
        with patch.object(frappe, "get_roles", return_value=["Sales User"]), \
             patch.object(frappe, "get_list") as query, self.assertRaises(ValueError):
            queue.get_ndr_options.__wrapped__("pe_shipkia_stage")
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
