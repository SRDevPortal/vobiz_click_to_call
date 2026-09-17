# Start recordings within the provider call flow

Outgoing mobile and browser calls previously started recording with a REST
request in Frappe's shared `short` queue. If that job ran late, the initial
conversation was never recorded. Callback history uses the same queue, so its
`received_at` field is the history-write time, not HTTP receipt time.

New answer XML puts `<Record recordSession="true" redirect="false"
playBeep="false" .../>` before `<Dial>`. This starts session capture as part of
the provider's answer instructions, without waiting for recording workers.
The recording includes pre-bridge audio/ringing. Existing format and recording
length limits are retained. The silence timeout is set to that same length
limit; it does not introduce a shorter silence cutoff.

Opening session recording can answer the browser SIP leg before the customer
answers. New browser call responses identify this mode. The browser displays
Connecting customer until the matching provider Dial answer is received; SDK
audio connection alone does not set customer answer time or Connected status.

The regular per-call authenticated recording and transcription callbacks remain
in use. Playback, downloads, call completion, End Call and disposition are not
replaced. Core incoming calls and conference recovery already use session
recording and retain their current flow. System Call's direct incoming dial
builders now also include session recording.

`recording_request_json.mode = "provider_session"` persists recording ownership
before returning XML. `Starting` means the instructions were prepared, not that
the provider has confirmed a recording. Only the normal completion callback
sets `Completed` and the final file information. XML is escaped, and callback
credentials are redacted from the saved request metadata.

Answer events skip REST recording jobs for calls with this marker. Already
queued jobs also check it. Existing REST recordings in Starting, Started or
Completed state are not replaced by a new XML recording. Repeated answer
requests before recording completion retain the session instruction without
resetting the saved state. Incoming fallback/retry behavior still needs to be
included in each site's controlled rollout tests.

Deploy both `vobiz_click_to_call` and `vobiz_system_call` together and restart
web/worker processes using the hosting platform's normal deployment process.
Build the System Call assets and refresh agent pages so the updated answer-state
handling is loaded along with the backend changes.
No new DocType fields or site-configuration switches are required. The existing
Enable Recording setting controls new calls. Avoid deployment during active
calls. This cannot restore audio missing from historical recordings.

Before production rollout, check outgoing browser calls, both mobile call
orders, incoming/fallback calls, End Call, saved disposition, playback,
transcription and recording channels. Include a call longer than two minutes
with more than a minute of silence, plus simultaneous-agent traffic. Verify
provider timestamps and decoded audio duration against the exact call UUID.

Provider reference: https://www.vobiz.ai/docs/xml/record

## Local verification, 17 September 2026

Backups and private evidence are in
`/home/jagmohan/.codex-work/provider-session-recording-20260917-095731`.

One authorised outgoing browser call, `CTC-ri2R2auNIkD1AArMFUF1DT8Q`, used
native session recording. The parent session lasted 180 seconds, including
about 10 seconds of ringing before the customer answered. The exact customer
leg lasted 170 seconds. The MP3 decoded fully to 180.216 seconds, stereo, and
provider recording timestamps covered the entire customer connection. Playback
through the authenticated ERP proxy returned the identical file.

The agent microphone was muted for 80 seconds; its recorded channel contains
81.5 seconds of continuous silence followed by resumed tone. The customer
confirmed the tone returned and the call stayed connected until End Call.
Customer-side sound was present, so this was not 80 seconds of two-sided
silence. End Call, saved disposition and release to Offline were verified.

That real test exposed early browser-leg answer, which led to the additional
provider-confirmed customer-state handling described above. That final status
change passed automated event-order tests; no second real call was made.
The checks passed 159 System Call Python tests, 119 core/integration Python test
entries (including two overlapping System Call tests), 157 JavaScript tests,
12 browser DOM checks, and the asset build. This is not a production-load test,
a real incoming/fallback test, or proof of all carrier behavior. No live site
has received these changes yet.
