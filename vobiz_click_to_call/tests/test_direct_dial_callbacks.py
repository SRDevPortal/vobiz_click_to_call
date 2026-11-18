"""Dialpad callbacks reject with Busy without touching agent reservations."""
from contextlib import ExitStack
import json
import unittest
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

import frappe
from vobiz_click_to_call.api import inbound
from vobiz_click_to_call.services import direct_dial


class DirectCallbackLookupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(direct_dial, "get_default_country_code", return_value="+91"))
        self.rows = self.stack.enter_context(patch.object(frappe, "get_all", return_value=[]))
        self.get_doc = self.stack.enter_context(patch.object(frappe, "get_doc"))

    def test_exact_customer_and_did_latest_outgoing_only(self):
        self.rows.return_value = [frappe._dict(name="DIRECT", request_json='{"direct_dial": true}')]
        result = direct_dial.outbound_only_callback("9876543210", "919262175560", {})
        self.assertIs(result, self.get_doc.return_value)
        self.get_doc.assert_called_once_with("Vobiz Call Log", "DIRECT")
        query = self.rows.call_args.kwargs
        self.assertEqual(query["filters"], {
            "source_app": "vobiz_click_to_call", "direction": "Outgoing",
            "normalized_customer_number": "+919876543210", "normalized_did": "+919262175560",
        })
        self.assertEqual(query["limit"], 1)
        self.assertEqual(query["order_by"], "creation desc, name desc")

    def test_normal_latest_call_does_not_search_back_to_old_dialpad_calls(self):
        for payload in ["{}", '{"direct_dial": false}', "[]", "invalid"]:
            with self.subTest(payload=payload):
                self.rows.return_value = [frappe._dict(name="NORMAL", request_json=payload)]
                self.assertIsNone(direct_dial.outbound_only_callback("9876543210", "9262175560", {}))
        self.get_doc.assert_not_called()
        self.assertEqual(self.rows.call_count, 4)

    def test_missing_history_allows_normal_routing(self):
        self.assertIsNone(direct_dial.outbound_only_callback("9876543210", "9262175560", {}))
        self.get_doc.assert_not_called()

    def test_missing_caller_or_did_never_queries_global_history(self):
        for caller, did in [("", "9262175560"), ("9876543210", "")]:
            self.assertIsNone(direct_dial.outbound_only_callback(caller, did, {}))
        self.rows.assert_not_called()

    def test_another_did_is_queried_separately(self):
        direct_dial.outbound_only_callback("9876543210", "9262175561", {})
        self.assertEqual(self.rows.call_args.kwargs["filters"]["normalized_did"], "+919262175561")


class DirectCallbackRoutingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.settings = frappe._dict(enabled=1)
        self.mock(inbound, "get_settings", lambda: self.settings)
        self.mock(inbound, "get_default_country_code", lambda *args: "+91")
        self.mock(inbound, "_inbound_callback_allowed", lambda *args: True)
        self.db = self.mock(frappe, "db", MagicMock())
        self.mock(frappe.utils, "now", lambda: "2026-11-18 12:00:00")
        self.previous = frappe._dict(name="DIRECT", user="ndr@example.test")
        self.existing = self.mock(inbound, "find_existing_inbound_call", MagicMock(return_value=None))
        self.lookup = self.mock(inbound, "outbound_only_callback", MagicMock(return_value=self.previous))
        self.create = self.mock(inbound, "create_inbound_call_log", MagicMock())
        self.reference = self.mock(inbound, "find_existing_reference", MagicMock(return_value=None))
        self.unknown = self.mock(inbound, "route_unknown_inbound", MagicMock())
        self.reserve = self.mock(inbound, "_mark_mapping_busy", MagicMock())
        self.notify = self.mock(inbound, "publish_callback_notification", MagicMock())
        self.restore = self.mock(inbound, "restore_mapping_after_call", MagicMock())
        self.payload = {"Event": "StartApp", "CallUUID": "INCOMING", "From": "919876543210", "To": "919262175560"}

    def mock(self, obj, name, value):
        return self.stack.enter_context(patch.object(obj, name, value))

    def assert_busy(self, response):
        xml = ElementTree.fromstring(response.get_data(as_text=True))
        self.assertEqual(len(xml), 1)
        self.assertEqual(xml[0].tag, "Hangup")
        self.assertEqual(xml[0].attrib, {"reason": "busy"})

    def test_reject_before_lead_patient_unknown_and_fallback_routing(self):
        self.assert_busy(inbound._route(self.payload))
        self.lookup.assert_called_once_with("+919876543210", "+919262175560", self.settings)
        self.assertTrue(self.create.call_args.kwargs["outbound_only"])
        self.reference.assert_not_called()
        self.unknown.assert_not_called()
        self.reserve.assert_not_called()
        self.notify.assert_not_called()
        self.restore.assert_not_called()
        self.db.commit.assert_called_once()

    def test_normal_callback_keeps_existing_reference_route(self):
        self.lookup.return_value = None
        self.reference.return_value = {"doctype": "CRM Lead", "name": "LEAD"}
        expected = object()
        self.unknown.return_value = expected
        self.assertIs(inbound._route(self.payload), expected)
        self.create.assert_not_called()

    def rejected(self):
        return frappe._dict(name="REJECTED", customer_number="+919876543210", did_number="+919262175560",
                            status="Busy", call_status="busy", request_json='{"direct_dial_callback_rejected": true}')

    def test_retry_reuses_busy_decision_without_new_log_or_mapping(self):
        self.existing.return_value = self.rejected()
        self.assert_busy(inbound._route(self.payload))
        self.lookup.assert_not_called()
        self.create.assert_not_called()
        self.reserve.assert_not_called()

    def test_retry_identity_mismatch_cannot_reuse_another_caller_decision(self):
        doc = self.rejected()
        doc.customer_number = "+919999999999"
        self.existing.return_value = doc
        xml = ElementTree.fromstring(inbound._route(self.payload).get_data(as_text=True))
        self.assertEqual(xml[0].tag, "Hangup")
        self.assertNotIn("reason", xml[0].attrib)
        self.lookup.assert_not_called()

    def test_late_provider_events_keep_busy_and_never_restore_an_agent_mapping(self):
        self.mock(inbound, "_lock_call_log", lambda doc: doc)
        self.mock(inbound, "snapshot_doc", lambda doc: dict(doc))
        self.mock(inbound, "save_doc_latest", lambda doc, before: doc)
        self.mock(inbound, "apply_provider_payload", lambda *args: None)
        self.mock(inbound, "status_from_provider", lambda *args, **kwargs: "Completed")
        for event in ["Hangup", "Ring"]:
            doc = self.rejected()
            self.existing.return_value = doc
            payload = dict(self.payload, Event=event, Status="completed")
            if event == "Hangup":
                inbound._route(payload)
            else:
                inbound.update_inbound_call_event(doc, payload)
            self.assertEqual(doc.status, "Busy")
            self.assertEqual(doc.hangup_cause, "DIALPAD_OUTBOUND_ONLY")
        self.restore.assert_not_called()
        self.reserve.assert_not_called()


class RejectedCallCreationTests(unittest.TestCase):
    def test_busy_status_and_replay_marker_are_set_before_insert(self):
        class Doc(frappe._dict):
            def insert(self, **kwargs):
                self.inserted_status = self.status
                return self
        with ExitStack() as stack:
            stack.enter_context(patch.object(frappe, "get_doc", side_effect=lambda values: Doc(values)))
            stack.enter_context(patch.object(frappe.utils, "now", return_value="2026-11-18 12:00:00"))
            stack.enter_context(patch.object(inbound, "make_outbound_call_key", return_value="KEY"))
            stack.enter_context(patch.object(inbound, "get_default_country_code", return_value="+91"))
            stack.enter_context(patch.object(inbound, "apply_provider_payload"))
            stack.enter_context(patch.object(inbound, "sync_reference_links"))
            doc = inbound.create_inbound_call_log(
                frappe._dict(user="ndr@example.test", caller_id="+919262175560"),
                "+919876543210", "+919262175560", "", {"CallUUID": "INCOMING"}, outbound_only=True,
            )
        self.assertEqual(doc.inserted_status, "Busy")
        self.assertEqual(doc.agent_number, "")
        self.assertTrue(json.loads(doc.request_json)["direct_dial_callback_rejected"])
        self.assertFalse(json.loads(doc.request_json)["inbound_mapped_agent"])
        self.assertEqual(doc.hangup_cause, "DIALPAD_OUTBOUND_ONLY")
