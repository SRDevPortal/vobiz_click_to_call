"""NDR authorization, exact permission-aware matching and standalone safety."""
import json
import unittest
from unittest.mock import MagicMock, patch
import frappe
from vobiz_click_to_call.services import direct_dial as direct, safety


class DirectDialTests(unittest.TestCase):
    def setUp(self):
        self.mock(frappe, 'session', frappe._dict(user='ndr@example.test'))
        self.mock(frappe, 'throw', lambda message, *args: (_ for _ in ()).throw(ValueError(message)))
        self.mock(direct, '_', lambda value: value)
        self.mock(safety, '_', lambda value: value)
        self.db = self.mock(frappe, 'db', MagicMock())
        self.mock(direct, 'get_allowed_doctypes', lambda _: ['CRM Lead', 'Patient'])
        self.mock(direct, 'get_default_country_code', lambda _: '+91')
        self.mock(frappe, 'has_permission', MagicMock(return_value=True))
        self.mock(frappe, 'get_meta', lambda _: frappe._dict(has_field=lambda _: True))
        self.rows = self.mock(frappe, 'get_list', MagicMock(return_value=[]))
        self.doc = frappe._dict(name='LEAD', mobile_no='+919876543210', has_permission=lambda _: True)
        self.get_doc = self.mock(frappe, 'get_doc', MagicMock(return_value=self.doc))
        self.mapping = frappe._dict(queue_source='NDR', enabled=1)
        self.settings = frappe._dict(enable_ndr_dialpad=1)
        self.mock(direct, 'get_settings', lambda: self.settings)

    def mock(self, obj, name, value):
        p = patch.object(obj, name, value)
        p.start(); self.addCleanup(p.stop)
        return value

    def test_only_enabled_ndr_mappings_qualify_including_mixed_queues(self):
        for sources in ['NDR', 'Patient\nNDR', 'CRM Lead,NDR']:
            self.assertTrue(direct.allowed(dict(queue_source=sources, enabled=1)))
        for mapping in [None, {}, {'queue_source': 'Patient'}, {'queue_source': 'NDR', 'enabled': 0}]:
            with self.assertRaises(ValueError): direct.assert_allowed(mapping)
        frappe.session.user = 'Guest'
        with self.assertRaises(ValueError): direct.resolve_target('9876543210', self.mapping, self.settings)
        self.rows.assert_not_called()

    def test_normalizes_domestic_trunk_and_international_numbers(self):
        for value in ['9876543210', '+91 98765-43210', '09876543210', '00919876543210', '919876543210']:
            self.assertEqual(direct.normalize_destination(value, self.settings), '+919876543210')
        self.assertEqual(direct.normalize_destination('+44 20 7946 0123', self.settings), '+442079460123')

    def test_rejects_incomplete_numbers_dial_commands_and_injected_urls(self):
        for value in [None, {}, '112', '1234567', '+911234567', '1234567890123456', '9876543210#', '*123#',
                      'sip:a@example.test', '+919876543210;123', '+919876543210x9', 'letters', '+00000000000']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                direct.normalize_destination(value, self.settings)

    def test_unknown_number_remains_standalone_without_document_creation(self):
        self.assertEqual(direct.resolve_target('9876543210', self.mapping, self.settings),
                         ('+919876543210', '', '', None))
        self.get_doc.assert_not_called()
        for query in self.rows.call_args_list:
            self.assertEqual(query.kwargs['limit_page_length'], 2)
            self.assertTrue(all(f[1] == 'in' for f in query.kwargs['or_filters']))

    def test_single_readable_match_links_without_modifying_it(self):
        self.rows.side_effect = [[frappe._dict(name='LEAD', mobile_no='9876543210')], []]
        self.assertEqual(direct.resolve_target('9876543210', self.mapping, self.settings),
                         ('+919876543210', 'CRM Lead', 'LEAD', 'mobile_no'))

    def test_ambiguous_matches_in_same_or_different_doctypes_are_not_linked(self):
        row = frappe._dict(name='LEAD', mobile_no='9876543210', mobile='9876543210')
        for matches in [[[row, row], []], [[row], [row]]]:
            self.rows.side_effect = matches
            self.assertEqual(direct.resolve_target('9876543210', self.mapping, self.settings)[1:], ('', '', None))
        self.get_doc.assert_not_called()

    def test_no_read_permission_skips_matching_entirely(self):
        frappe.has_permission.return_value = False
        self.assertEqual(direct.resolve_target('9876543210', self.mapping, self.settings)[1:], ('', '', None))
        self.rows.assert_not_called()

    def test_document_permission_or_phone_change_prevents_stale_link(self):
        for changed in [dict(has_permission=lambda _: False), dict(mobile_no='9123456789')]:
            self.doc.update(changed)
            self.rows.side_effect = [[frappe._dict(name='LEAD', mobile_no='9876543210')], []]
            self.assertEqual(direct.resolve_target('9876543210', self.mapping, self.settings)[1:], ('', '', None))
            self.doc.update(has_permission=lambda _: True)

    def test_marker_parsing_fails_closed_for_legacy_payloads(self):
        for value in ['', 'invalid', '[]', '{"direct_dial": "true"}']:
            self.assertFalse(direct.is_direct_call(dict(request_json=value)))
        self.assertTrue(direct.is_direct_call(dict(request_json='{"direct_dial": true}')))

    def test_standalone_daily_limit_counts_destination_not_all_blank_references(self):
        self.mock(safety, 'today_bounds', lambda: ('start', 'end'))
        self.mock(safety, 'get_working_hours_block_reason', lambda _: '')
        self.db.count.return_value = 2
        settings = frappe._dict(max_call_attempts_per_reference_per_day=2)
        with self.assertRaisesRegex(ValueError, 'Daily call attempt'):
            safety.assert_call_allowed(customer_number='+919876543210', reference_doctype='', reference_name='',
                                       user='ndr@example.test', mapping=self.mapping, settings=settings)
        self.db.count.assert_called_once_with('Vobiz Call Log', {
            'customer_number': '+919876543210', 'creation': ['between', ('start', 'end')]})

    def test_standalone_blocked_destination_is_rejected(self):
        block = self.mock(safety, 'get_blocked_reason', MagicMock(return_value='Do not call'))
        with self.assertRaisesRegex(ValueError, 'Do not call'):
            safety.assert_call_allowed(customer_number='+919876543210', reference_doctype='', reference_name='',
                                       user='ndr@example.test', mapping=self.mapping,
                                       settings=frappe._dict(prevent_blocked_numbers=1))
        block.assert_called_once_with('+919876543210', '', '')

    def test_retry_of_standalone_call_does_not_relink_a_newly_created_record(self):
        self.doc.update(user=frappe.session.user, request_json='{"direct_dial":true}', customer_number='+919876543210',
                        reference_doctype=None, reference_name=None, phone_field=None)
        self.assertEqual(direct.resolve_retry_target('OLD', self.mapping, self.settings), ('+919876543210', '', '', None))
        self.rows.assert_not_called()

    def test_linked_retry_rechecks_permission_and_current_number(self):
        previous = frappe._dict(user=frappe.session.user, request_json='{"direct_dial":true}', customer_number='+919876543210',
                               reference_doctype='CRM Lead', reference_name='LEAD', phone_field='mobile_no')
        self.get_doc.side_effect = [previous, self.doc]
        self.assertEqual(direct.resolve_retry_target('OLD', self.mapping, self.settings)[1:], ('CRM Lead', 'LEAD', 'mobile_no'))
        self.doc.mobile_no = '9123456789'
        self.get_doc.side_effect = [previous, self.doc]
        with self.assertRaisesRegex(ValueError, 'changed'): direct.resolve_retry_target('OLD', self.mapping, self.settings)

    def test_foreign_retry_is_rejected_before_destination_matching(self):
        self.doc.update(user='someone-else', request_json='{"direct_dial":true}')
        with self.assertRaisesRegex(ValueError, 'Not permitted'): direct.resolve_retry_target('OLD', self.mapping, self.settings)
        self.rows.assert_not_called()

    def test_mobile_bridge_rechecks_revoked_ndr_access_after_lock(self):
        from types import SimpleNamespace
        from vobiz_click_to_call.api import call
        self.mock(frappe, 'local', SimpleNamespace(flags=frappe._dict(in_test=False), request=None))
        self.mock(call, 'log_vobiz_event', MagicMock())
        self.mock(call, 'get_settings', lambda: frappe._dict(enabled=1, enable_ndr_dialpad=1))
        self.mapping.update(name='MAP', user=frappe.session.user)
        self.mock(call, 'get_user_mapping', lambda _: self.mapping)
        self.mock(call, '_lock_user_mapping', MagicMock())
        fresh = frappe._dict(self.mapping, queue_source='Patient')
        fresh.as_dict = lambda: dict(fresh)
        self.get_doc.return_value = fresh
        created = self.mock(call, 'create_call_log', MagicMock())
        with self.assertRaisesRegex(ValueError, 'NDR'): call.start_direct_call('9876543210')
        self.get_doc.assert_called_once_with('Vobiz User Mapping', 'MAP', for_update=True)
        created.assert_not_called()

    def test_setting_off_or_missing_denies_ndr_button_and_api_before_lookup(self):
        for value in (0, "0", None):
            self.settings.enable_ndr_dialpad = value
            self.assertFalse(direct.allowed(self.mapping))
            with self.assertRaisesRegex(ValueError, 'Enable NDR Dialpad'):
                direct.resolve_target('9876543210', self.mapping, self.settings)
            with self.assertRaisesRegex(ValueError, 'Enable NDR Dialpad'):
                direct.resolve_retry_target('OLD', self.mapping, self.settings)
        self.rows.assert_not_called()
        self.get_doc.assert_not_called()
        self.settings.enable_ndr_dialpad = 1
        self.assertTrue(direct.allowed(self.mapping))

    def test_console_eligibility_reads_updated_setting_without_restart(self):
        self.assertTrue(direct.allowed(self.mapping))
        self.settings.enable_ndr_dialpad = 0
        self.assertFalse(direct.allowed(self.mapping))
        self.settings.enable_ndr_dialpad = 1
        self.assertTrue(direct.allowed(self.mapping))
