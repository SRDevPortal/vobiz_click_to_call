# Callback history and mapping restoration

Implemented in the local bench checkouts after pulling GitHub develop:
`vobiz_click_to_call` through `e448f6d`, and `vobiz_ai` through `2a1aa80`.
The pulls retained pre-existing local commits and working edits. Callback history
now has one shared implementation in `vobiz_ai`; the click-to-call worker delegates
to it. Mapping recovery remains in `vobiz_click_to_call`.
No production settings, call records, endpoints, or credentials were changed.
`vobiz_system_call` was checked against GitHub develop at
`0432d6adac1d457b80c764efecaff7f6ca9296d8`; that checkout remains unchanged.

## Changes

- History appends lock and update only the call-log history columns. They do not
  run document-save hooks, update call status, or advance the call activity timestamp.
  Concurrent appends read the latest history under the row lock and retain the last 50 events.
- Legacy mapping restoration queues work after the call transaction commits.
  Each restore job locks one mapping before its call log, checks the current pointer
  under that lock, and conditions the update on that same pointer. Late callbacks
  cannot release a newer call. Offline/Away choices and browser presence are preserved.
- Deadlocks and lock timeouts in history/restoration jobs request Frappe's bounded
  whole-job rollback/retry. Database errors are no longer swallowed by the AI restore hook.
- The existing minute scheduler now scans a bounded, resumable batch of mappings
  and queues guarded recovery, including missing or already terminal call records.
  Provider calls are reconciled outside database locks. Active or uncertain calls
  are not declared ended solely because their ringing timestamp is old.
- Automatic legacy CDR reconciliation requires an exact provider identifier and
  terminal evidence. Another call to the same phone number cannot release the agent.
  Missing provider evidence remains pending for reconciliation/operator investigation.
- Read timeouts during caller-ID fallback get the same Confirmation Pending handling
  as the first attempt. An uncertain call-creation POST is never automatically repeated.
- Conflict merging uses a locking read instead of an old REPEATABLE READ snapshot.
  During conflict merging, a delayed nonterminal event cannot reopen a terminal call.
- Agents receive a private availability-refresh event after restoration commits.

These changes address the observed application races; they cannot prevent a provider
outage or prove that an unacknowledged provider request never created a call.

## Validation

- Latest focused run: 103 tests passed, including callback history, upstream AI
  scheduler/schema tests, mapping recovery, callback enqueue, webhook status mapping,
  and system-call safety. All 3 real MariaDB concurrency tests also passed.
- Real MariaDB tests use randomly named disposable tables and independent connections
  under REPEATABLE READ: 12 simultaneous history appends; history versus mapping
  restoration; and late restoration racing with a newer reservation. No business rows
  are modified by these tests.
- Two unrelated source-test failures also reproduce with unmodified Git HEAD sources:
  `test_polling_does_not_hit_provider_or_overlap` expects an obsolete console-route gate;
  `test_unknown_lead_defaults_follow_vobiz_ai_defaulting` expects removed arbitrary
  pipeline/platform defaults. Their assertions and the corresponding behavior were
  not changed as part of this fix.

Run the database tests only against an explicitly selected local test site:

```sh
VOBIZ_TEST_SITE=localhost VOBIZ_TEST_SITES_PATH=/path/to/frappe-bench/sites \
  ./env/bin/python -m unittest vobiz_click_to_call.tests.test_database_concurrency
```

## Deployment

Deploy both updated apps together, migrate the intended site, build the
`vobiz_click_to_call` assets, and restart web and background workers so old workers
do not retain the previous hook code. Ensure the short queue and scheduler run.

The migration `replace_unsafe_stale_cleanup` disables only the known scheduled
Server Scripts `Vobiz Stale Call Cleanup` and `Ringing Call Connect Issue`, and only
when they contain the expected mapping/current-call recovery code. The scripts
are retained for audit. This removes the competing call-log-first bulk updater;
the app scheduler replaces it. Review any other custom cleanup scripts separately.

After deployment, check new callback/mapping error rates, recovery job completion,
and mappings still pointing to terminal/missing calls. Test a controlled outbound
call and its terminal callback before declaring production recovery verified.
Provider credentials and duplicate browser endpoint assignments require their own
configuration correction; this code change does not reassign agent identities.
