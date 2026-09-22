from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse, parse_qs
from xml.etree import ElementTree

import frappe
from redis.exceptions import LockError
from werkzeug.wrappers import Response

from vobiz_click_to_call.services import incoming_routing as routing, debug_log
from vobiz_click_to_call.api import inbound


class IncomingRoutingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.flags = frappe._dict()
        self.mock(frappe, "flags", self.flags)
        self.db = self.mock(frappe, "db", MagicMock())
        self.db.sql.return_value = [(50,)]
        self.cache = MagicMock()
        self.cache.make_key.side_effect = lambda key: "SITE|" + key
        self.cache.get_value.return_value = None
        self.locks = []
        def lock(*args, **kwargs):
            result = MagicMock()
            result.acquire.return_value = True
            self.locks.append(result)
            return result
        self.cache.lock.side_effect = lock
        self.mock(frappe, "cache", lambda: self.cache)
        self.enqueue = self.mock(frappe, "enqueue", MagicMock())
        self.mock(frappe, "logger", MagicMock())
        self.mock(frappe.utils, "now", lambda: "2026-09-22 12:00:00")
        self.payload = dict(CallUUID="UUID-A", From="919873090386", To="+919262175560",
                            token="test-secret", Event="StartApp")
        self.endpoint = "https://example.test/api/method/answer"

    def mock(self, obj, name, value):
        return self.stack.enter_context(patch.object(obj, name, value))

    def run_route(self, handler, payload=None):
        return routing.run(payload or self.payload, handler, self.endpoint)

    def test_no_did_mutex_and_scoped_database_wait_restored(self):
        result = self.run_route(lambda: Response("<Response><Dial/></Response>"))
        self.assertEqual(result.status_code, 200)
        names = [x.args[0] for x in self.cache.lock.call_args_list]
        self.assertEqual(len(names), 2)
        self.assertTrue(all(x.startswith("SITE|") for x in names))
        self.assertFalse(any("incoming-route" in x for x in names))
        for lock in self.locks:
            lock.acquire.assert_called_once_with(blocking=False)
            lock.release.assert_called_once()
        self.assertEqual(self.db.sql.call_args_list[-1].args,
                         ("SET SESSION innodb_lock_wait_timeout = %s", (50,)))
        self.assertFalse(self.flags.vobiz_incoming_routing)

    def test_contention_returns_bounded_valid_redirect_without_routing(self):
        lock = MagicMock()
        lock.acquire.return_value = False
        self.cache.lock.side_effect = None
        self.cache.lock.return_value = lock
        handler = MagicMock()
        result = self.run_route(handler)
        root = ElementTree.fromstring(result.get_data(as_text=True))
        self.assertEqual(root.find("PreAnswer/Wait").attrib["length"], "1")
        query = parse_qs(urlparse(root.find("Redirect").text).query)
        self.assertEqual(query["CallUUID"], ["UUID-A"])
        self.assertEqual(query["vobiz_route_attempt"], ["1"])
        self.assertEqual(query["token"], ["test-secret"])
        handler.assert_not_called()
        lock.release.assert_not_called()
        last = self.run_route(handler, dict(self.payload, vobiz_route_attempt=routing.MAX_RETRIES))
        self.assertIsNotNone(ElementTree.fromstring(last.get_data(as_text=True)).find("Hangup"))

    def test_second_lock_busy_releases_first(self):
        a, b = MagicMock(), MagicMock()
        a.acquire.return_value = True
        b.acquire.return_value = False
        self.cache.lock.side_effect = [a, b]
        handler = MagicMock()
        self.run_route(handler)
        a.release.assert_called_once()
        b.release.assert_not_called()
        handler.assert_not_called()

    def test_database_deadlock_rolls_back_and_retries(self):
        def failed():
            raise frappe.QueryDeadlockError("fixture")
        reply = self.run_route(failed)
        self.db.rollback.assert_called_once()
        self.assertIn("<Redirect", reply.get_data(as_text=True))
        self.assertEqual(self.db.sql.call_args_list[-1].args[1], (50,))

    def test_programming_error_is_not_hidden_as_a_busy_call(self):
        def failed():
            raise ValueError("bug")
        with self.assertRaisesRegex(ValueError, "bug"):
            self.run_route(failed)
        self.assertEqual(self.db.sql.call_args_list[-1].args[1], (50,))
        self.assertTrue(all(x.release.call_count == 1 for x in self.locks))

    def test_hangup_marker_stops_delayed_routing_and_retries(self):
        self.cache.get_value.return_value = True
        handler = MagicMock()
        reply = self.run_route(handler)
        self.assertIn("<Hangup", reply.get_data(as_text=True))
        handler.assert_not_called()
        self.assertIn("<Hangup", routing.retry_response(self.payload, self.endpoint).get_data(as_text=True))

    def test_expired_lease_does_not_replace_committed_response_with_500(self):
        lock = MagicMock()
        lock.acquire.return_value = True
        lock.release.side_effect = LockError("expired")
        self.cache.lock.side_effect = None
        self.cache.lock.return_value = lock
        result = self.run_route(lambda: Response("committed"))
        self.assertEqual(result.get_data(as_text=True), "committed")

    def test_diagnostics_never_insert_under_routing_locks(self):
        new_doc = self.mock(frappe, "new_doc", MagicMock())
        observed = []
        def enqueue(*args, **kwargs):
            observed.append(all(x.release.call_count == 1 for x in self.locks))
        self.enqueue.side_effect = enqueue
        def handler():
            debug_log.log_vobiz_event("slow warning", severity="Warning", payload={"CallUUID": "UUID-A"})
            new_doc.assert_not_called()
            self.enqueue.assert_not_called()
            return Response("OK")
        self.run_route(handler)
        self.assertEqual(observed, [True])
        new_doc.assert_not_called()
        self.assertEqual(self.enqueue.call_args.kwargs["queue"], "default")

    def test_diagnostic_enqueue_failure_preserves_answer(self):
        self.enqueue.side_effect = RuntimeError("queue down")
        def handler():
            debug_log.log_vobiz_event("warning", severity="Warning")
            return Response("OK")
        self.assertEqual(self.run_route(handler).get_data(as_text=True), "OK")

    def test_lost_agent_race_does_not_overwrite_another_call(self):
        self.db.sql.side_effect = [[], [("NEWER-CALL",)]]
        with self.assertRaises(routing.RouteContended):
            routing.reserve_agent("agent@example.test", "THIS-CALL")
        update = self.db.sql.call_args_list[0].args[0]
        self.assertIn("IFNULL(current_call_log, '')=''", update)
        self.assertIn("accept_calls=1", update)
        self.assertIn("enabled=1", update)

    def test_owned_reservation_is_idempotent(self):
        self.db.sql.side_effect = [[], [("THIS-CALL",)]]
        routing.reserve_agent("agent@example.test", "THIS-CALL")

    def test_no_reservation_after_lease_expires(self):
        lock = MagicMock()
        lock.owned.return_value = False
        self.flags.vobiz_incoming_locks = [lock]
        with self.assertRaises(routing.RouteContended):
            routing.reserve_agent("agent@example.test", "THIS-CALL")
        self.db.sql.assert_not_called()


class IncomingReplayTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.payload = dict(CallUUID="UUID", From="919873090386", To="919262175560", Event="StartApp")
        self.doc = frappe._dict(name="CALL", user="agent", status="Agent Ringing",
                               call_status="ringing", agent_number="sip:agent@example.test",
                               user_mobile="+919999999999", request_json='{}')
        self.mock(inbound, "get_settings", lambda: frappe._dict(enabled=1))
        self.mock(inbound, "get_default_country_code", lambda *args: "91")
        self.mock(inbound, "_inbound_callback_allowed", lambda *args: True)
        self.mock(inbound, "find_existing_inbound_call", lambda payload: self.doc)
        self.mock(inbound, "get_user_mapping", lambda user: {"current_call_log": "CALL"})
        self.lookup = self.mock(inbound, "find_existing_reference", MagicMock())
        self.dial = self.mock(inbound, "_dial_agent_xml", MagicMock(return_value="<Response><Dial/></Response>"))

    def mock(self, obj, name, value):
        return self.stack.enter_context(patch.object(obj, name, value))

    def test_repeat_uses_existing_agent_without_reselecting(self):
        inbound._route(self.payload)
        self.lookup.assert_not_called()
        self.dial.assert_called_once_with(self.doc, "+919999999999", unittest.mock.ANY)

    def test_repeat_cannot_reopen_terminal_or_cancelled_calls(self):
        for status, call_status in [("Completed", ""), ("Agent Ringing", "cancellation-requested")]:
            self.doc.status, self.doc.call_status = status, call_status
            result = inbound._route(self.payload)
            self.assertIn("Hangup", result.get_data(as_text=True))
        self.dial.assert_not_called()

    def test_repeat_cannot_dial_agent_reserved_by_newer_call(self):
        self.mock(inbound, "get_user_mapping", lambda user: {"current_call_log": "NEW"})
        self.assertIn("Hangup", inbound._route(self.payload).get_data(as_text=True))
        self.dial.assert_not_called()

    def test_external_fallback_replay_preserves_existing_target(self):
        self.doc.request_json = '{"inbound_mapped_agent": false}'
        self.doc.agent_number = "+918888888888"
        self.mock(inbound, "get_user_mapping", lambda user: None)
        inbound._route(self.payload)
        self.dial.assert_called_once_with(self.doc, "+918888888888", unittest.mock.ANY)

    def test_late_retry_cannot_redial_an_answered_call(self):
        self.doc.status = "Connected"
        self.assertNotIn("<Dial", inbound._route(self.payload).get_data(as_text=True))
        self.dial.assert_not_called()

    def test_retry_with_mismatched_caller_cannot_borrow_route(self):
        self.doc.customer_number = "+919999999999"
        self.assertIn("Hangup", inbound._route(self.payload).get_data(as_text=True))
        self.dial.assert_not_called()
