# Manual transcript recovery

Adds **Sync Missing Transcriptions** to Vobiz Settings and the Vobiz Call Log
list. Only System Managers can create/start/pause/resume a bulk run.

## Use

1. Open Vobiz Settings → Sync Missing Transcriptions → Add Vobiz Transcript Sync.
2. Select From date, To date, **Maximum call logs** (default 1,000; allowed
   1–100,000) and Calls per batch (default 25; allowed 1–50). A total limit of
   10,000 with batch size 25 means up to 400 batches. The total counts every log
   checked, including skipped/unavailable/failed calls; it is not a promise of
   that many recovered transcripts. The run stops at the total limit or at the
   end of the date range, whichever comes first.
   Dates refer to **call-log creation time in the site timezone**, inclusive,
   with at most 31 days per run. Save the draft.
3. Preview Next Calls checks at most 100 next call logs locally. It is a sample,
   not a backlog count, and does not call Vobiz.
4. Start Sync queues work on the long queue. Use Refresh Progress and View
   Results to see outcomes. Pause/Resume preserves the cursor.
5. A completed run can be repeated by creating a new run for the same dates,
   useful when the provider generates a previously unavailable transcript later.

No scan, transcript request or historical update starts during installation.

## Behaviour

- Reads existing transcripts through the authenticated account API:
  GET /Account/{auth_id}/Transcriptions/?call_uuid=...
  and, when needed, GET /Account/{auth_id}/Transcription/{id}/transcription/.
  These routes were observed in Vobiz's current console and verified read-only
  against Bharat on 22 October 2026. No paid generation POST is sent.
- Requires exact provider call UUID and account matches. A supplied recording ID
  must also agree. Legacy logs with blank account_id are accepted only when the
  provider account matches the configured account and the UUID matches.
- Checks recording_call_uuid, call_uuid, b_leg_uuid and a_leg_uuid; never phones,
  time proximity, browser events, arbitrary transcript URLs or CDR scans.
- Preserves either existing transcript text field. Saves only transcript_text,
  transcription_text, transcript_status, transcript_received_at, transcript_error
  and, if blank, transcription_id. Bypasses save/on_update hooks deliberately.
- Does not change manual dispositions, call/lead status, availability, billing,
  recording fields or AI status, and does not enqueue AI classification.
- Logs missing provider text as Unavailable, identity/schema conflicts as Failed,
  existing text/active calls/wrong accounts/no UUID as Skipped.
- Each batch checks up to the configured number of logs and yields after about
  45 seconds between calls. Each call update, audit result and cursor commit
  together. Provider HTTP finishes before any database row lock is taken.
- Scheduler continues active runs once per minute. Requires an enabled scheduler
  and a worker consuming long. No new queue configuration is needed if an
  existing worker already consumes that queue.
- Only one active run per site. Redis locks and queue deduplication prevent
  overlapping workers for a run. Pause discards an in-flight provider response
  before saving when the pause has already committed.
- Temporary errors retry the same call with saved progress and backoff; three
  provider failures mark that call Failed and continue on the next tick. Local
  shared API-budget deferrals do not consume retry attempts.
- Access errors stop the run. Review Error Log and Resume once fixed.
  A changed Vobiz account requires a new run.
- Uses the existing creation index and a (creation, name) keyset cursor, never
  OFFSET or a full-backlog COUNT. The snapshot excludes later-created records.
- Provider reads share the app's account rate budget. Use a small batch during
  busy periods. Background work still uses worker/API/database capacity.

This recovers missed data; it does not repair the original callback-delivery
problem or create transcripts that Vobiz has not generated/retained. Newly
available transcripts and currently active calls need another run later.

## Apply the patch

Based on vobiz_click_to_call develop commit 2794c70. Preserve local work and
integrate upstream changes first. From the bench directory, replace the patch
path and site name below with your deployment values:

    git -C apps/vobiz_click_to_call apply --check /path/to/vobiz-transcript-sync-20261022.patch
    git -C apps/vobiz_click_to_call apply /path/to/vobiz-transcript-sync-20261022.patch
    bench --site YOUR_SITE migrate
    bench build --app vobiz_click_to_call
    bench --site YOUR_SITE clear-cache
    bench restart

Use the deployment platform's normal restart/build workflow if Bench does not
manage processes directly. Migration creates two new DocTypes (Vobiz Transcript
Sync and Vobiz Transcript Sync Result) and registers the scheduler. It does not
add fields to or bulk-update existing call logs.

Start with one day and batch size 5, inspect recovered records and manual
dispositions, then increase to 25. Keep the pre-deployment database backup and
deploy during your normal maintenance window.

## Rollback

Pause every active sync first and let in-flight jobs exit. Revert the feature's
code, build assets, migrate scheduler definitions and restart workers. Existing
recovered transcripts and audit/progress records remain; reverting code does
not erase them. No automatic transcript deletion is included.

## Validation

32 provider/worker/permission/database tests, 2 UI-control tests and 40 existing
CDR identity/webhook tests passed locally; asset build passed. Database tests
verify rollback and resume, exact-once result creation, no disposition/status
changes, and completed-run progress. Live Vobiz checks were read-only; no Bharat
call logs were modified and no calls or new transcriptions were requested.
