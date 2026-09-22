# Incoming routing contention fix

An incoming request previously held a Redis mutex shared by every caller to a DID
across all routing work, including synchronous warning-log inserts. A slow
database write therefore prevented unrelated calls from routing.

## Behavior

- Both the shared click-to-call inbound entry point and the system-call browser
  adapter use the same routing guard. Outgoing browser calls retain their existing path.
- Site-scoped locks protect one provider call and one normalized caller. The
  caller lock prevents duplicate unknown-caller lead creation across DIDs.
  There is no whole-DID routing lock.
- Agent reservation uses a conditional database UPDATE followed by a locking
  read. A stale candidate cannot overwrite an outgoing call or another incoming
  reservation. Existing owner, patient, fallback and DID routing priorities remain.
  Round-robin bookkeeping follows successful reservation.
- Contention rolls back and returns XML with a one-second PreAnswer/Wait and a
  redirect to the same routing entry point. At most four redirects are requested.
  Provider call identity and authentication are preserved. Persistent contention
  returns Hangup XML; it does not mark the call completed without provider evidence.
- The database row-lock wait is temporarily limited to two seconds per statement
  and restored on exit. This is not a total request-duration guarantee.
- Duplicate requests reuse the existing routing record. Terminal/cancelled calls,
  mismatched identities and an agent's newer reservation cannot be reopened.
  Hangup markers prevent delayed retries from starting after termination.
- Routing diagnostics are queued on the existing default worker after routing
  locks have been released. Logging is best effort: enqueue failure does not
  change the caller's routing response.
- Slow/retried requests log elapsed time, retry reason, attempt and call UUID to
  the vobiz_incoming application logger without writing a diagnostic DB row.
- A migration adds any missing leading indexes for exact CallUUID, SIPCallID and
  request-ID lookups. Existing equivalent indexes are retained.

Vobiz early media support depends on the carrier/number. A pilot must confirm the
caller experience and billing/answer timing during retry; PreAnswer is not a
guarantee that every carrier will keep the call unanswered.
Provider reference: https://www.vobiz.ai/docs/xml/preanswer

## Deployment

Deploy matching versions of vobiz_click_to_call and vobiz_system_call together.
Back up the site, run the normal site migration, and restart all web/worker
processes through the server's process manager. Drain old in-flight routing
requests; do not run mixed old/new routing implementations during the pilot.
The index patch uses InnoDB online DDL (ALGORITHM=INPLACE, LOCK=NONE); migration
errors must be resolved, not ignored. No new dedicated worker is required:
the existing default worker must be running for deferred diagnostics.

Example from the bench directory:

    bench --site YOUR_SITE migrate
    bench restart

## Validation performed locally

- 187 system-call Python tests passed.
- 18 targeted routing/replay/diagnostic unit tests passed.
- 8 opt-in real MariaDB/Redis tests passed, including 20 concurrent independent
  agent reservations, competing reservations of one agent, contention against an
  outgoing call, and a stalled caller while another caller on the same DID progresses.
- The index migration ran twice successfully on localhost (second run no-op).
- Local /api/method/ping returned HTTP 200, pong.
- The full click-to-call suite: 319 tests, 305 passed, 12 skipped, two failures.
  Both failures reproduce against the pre-change source backup:
  test_unknown_lead_defaults_follow_vobiz_ai_defaulting and
  test_polling_does_not_hit_provider_or_overlap.
  These existing source-assertion failures were not hidden or changed for this fix.

Run the focused tests from the bench directory:

    env/bin/python -m unittest vobiz_click_to_call.tests.test_incoming_routing
    VOBIZ_TEST_SITE=localhost VOBIZ_TEST_SITES_PATH="$PWD/sites" env/bin/python -m unittest vobiz_click_to_call.tests.test_incoming_routing_database

The integration tests use disposable, randomly named InnoDB tables and Redis lock
names. They do not call customers or change existing agents.

## Controlled pilot still required

Test real incoming pickup, hangup and disposition; caller abandonment during
retry; patient/lead/unknown-caller routing and fallbacks; and recording. Observe
multiple simultaneous incoming callers to the same DID and mixed outgoing traffic.
Verify one reservation and call log per call, no repeated agent dialing, and
provider confirmation before terminal-state/disposition handling.

This change isolates application contention. It does not repair a database-wide
outage or identify the underlying Bharat blocking transaction; that still needs
the database administrator's lock/deadlock snapshot.

No live site was modified while implementing or testing this change.
