"""Bounded, per-caller inbound routing; unrelated callers never share a DID lock."""
from __future__ import annotations

from hashlib import sha256
from urllib.parse import urlencode
from xml.sax.saxutils import escape
import time

import frappe
from redis.exceptions import RedisError
from werkzeug.wrappers import Response

from vobiz_click_to_call.services.debug_log import defer_diagnostics
from vobiz_click_to_call.services.numbers import normalize_phone_number

MAX_RETRIES = 4
LOCK_WAIT_SECONDS = 2


class RouteContended(Exception):
    """Another transaction reserved this agent after target selection."""


def _uuid(payload):
    return str(payload.get("CallUUID") or payload.get("call_uuid") or payload.get("uuid")
               or payload.get("SIPCallID") or payload.get("sip_call_id") or "")


def _key(kind, value):
    return "vobiz:inbound:" + kind + ":" + sha256(value.encode()).hexdigest()


def mark_ended(payload):
    uuid = _uuid(payload)
    if uuid:
        frappe.cache().set_value(_key("ended", uuid), True, expires_in_sec=86400)


def ended(payload):
    uuid = _uuid(payload)
    return bool(uuid and frappe.cache().get_value(_key("ended", uuid), expires=True))


def _xml(body):
    return Response('<?xml version="1.0" encoding="UTF-8"?><Response>' + body + '</Response>',
                    mimetype="text/xml")


def retry_response(payload, endpoint):
    # Redirect carries only routing identity, not the entire provider payload.
    attempt = max(0, frappe.utils.cint(payload.get("vobiz_route_attempt")))
    if attempt >= MAX_RETRIES:
        return _xml("<Hangup/>")
    try:
        if ended(payload):
            return _xml("<Hangup/>")
    except RedisError:
        pass
    query = {k: payload[k] for k in (
        "token", "Token", "callback_token", "inbound_token", "inbound_callback_token",
        "CallUUID", "call_uuid", "uuid", "From", "from", "To", "to",
        "Caller", "caller_id", "Called", "did", "DID", "SIPCallID", "RequestUUID", "RequestID",
    ) if payload.get(k)}
    query.update(Event="StartApp", vobiz_route_attempt=attempt + 1)
    url = endpoint + "?" + urlencode(query)
    return _xml('<PreAnswer><Wait length="1"/></PreAnswer><Redirect method="POST">' + escape(url) + '</Redirect>')


def run(payload, handler, endpoint):
    """Call only after authenticating the provider webhook.

    The caller lock prevents duplicate unknown leads across DIDs; UUID lock
    protects duplicate callbacks. Agent reservation is a conditional DB update
    shared with all inbound routes, so there is no whole-DID routing mutex.
    """
    if getattr(frappe.flags, "vobiz_incoming_routing", False):
        return handler()
    uuid = _uuid(payload)
    caller = normalize_phone_number(str(payload.get("From") or payload.get("from")
                                      or payload.get("Caller") or payload.get("caller_id") or ""))
    if not uuid or not caller:
        return _xml("<Hangup/>")
    started = time.monotonic()
    previous_timeout = None
    retry_cause = ""
    locks = []
    cache = frappe.cache()
    try:
        with defer_diagnostics():
            try:
                for kind, value in (("call", uuid), ("caller", caller)):
                    lock = cache.lock(cache.make_key(_key(kind, value)), timeout=120, blocking_timeout=0)
                    if not lock.acquire(blocking=False):
                        raise RouteContended()
                    locks.append(lock)
                previous_timeout = frappe.db.sql("SELECT @@SESSION.innodb_lock_wait_timeout")[0][0]
                frappe.db.sql("SET SESSION innodb_lock_wait_timeout = %s", (LOCK_WAIT_SECONDS,))
                frappe.flags.vobiz_incoming_routing = True
                frappe.flags.vobiz_incoming_locks = locks
                if ended(payload):
                    return _xml("<Hangup/>")
                return handler()
            except (RouteContended, frappe.QueryTimeoutError, frappe.QueryDeadlockError, RedisError) as exc:
                retry_cause = type(exc).__name__
                frappe.db.rollback()
                return retry_response(payload, endpoint)
            finally:
                frappe.flags.vobiz_incoming_routing = False
                frappe.flags.vobiz_incoming_locks = None
                # Restore even on errors: the connection can be reused by Frappe.
                try:
                    if previous_timeout is not None:
                        frappe.db.sql("SET SESSION innodb_lock_wait_timeout = %s", (previous_timeout,))
                finally:
                    for lock in reversed(locks):
                        try:
                            lock.release()
                        except RedisError:
                            # Never release someone else's replacement lease or
                            # turn an already-committed answer into HTTP 500.
                            _warn("Incoming routing lease expired", uuid)
    finally:
        elapsed = time.monotonic() - started
        if retry_cause or elapsed >= 1:
            _warn("Incoming routing took %.3fs; retry=%s; attempt=%s" % (
                elapsed, retry_cause or "none", frappe.utils.cint(payload.get("vobiz_route_attempt")),
            ), uuid)


def _warn(message, uuid):
    try:
        frappe.logger("vobiz_incoming").warning("%s; call_uuid=%s", message, uuid)
    except Exception:
        pass


def reserve_agent(user, call_log):
    """Atomically recheck availability, including against outgoing reservations.

    This UPDATE obtains the mapping's row lock. The WHERE predicate is evaluated
    against the latest committed row after waiting, never a stale routing read.
    """
    for lock in getattr(frappe.flags, "vobiz_incoming_locks", None) or []:
        if not lock.owned():
            raise RouteContended()
    frappe.db.sql(
        """UPDATE `tabVobiz User Mapping`
           SET availability_status='Busy', accept_calls=0, current_call_log=%s,
               last_status_at=%s, modified=%s
           WHERE user=%s AND enabled=1
             AND (current_call_log=%s OR
                  (IFNULL(current_call_log, '')='' AND accept_calls=1
                   AND COALESCE(NULLIF(availability_status, ''), 'Available')='Available'))""",
        (call_log, frappe.utils.now(), frappe.utils.now(), user, call_log),
    )
    # A locking read sees the current version under REPEATABLE READ as well.
    rows = frappe.db.sql(
        "SELECT current_call_log FROM `tabVobiz User Mapping` WHERE user=%s AND enabled=1 FOR UPDATE",
        (user,),
    )
    if len(rows) != 1 or rows[0][0] != call_log:
        raise RouteContended()
