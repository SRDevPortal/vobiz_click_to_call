# Call recovery and provider outages

These changes work together with `vobiz_system_call`. They do not automatically
retry outbound call creation or declare a call ended because a request timed out.

- Recovery reads use the Vobiz Settings HTTP timeout (20 seconds locally), with
  a separate connection timeout of at most 5 seconds. The old 3-second overrides
  are removed. Read timeouts, connection errors, HTTP 408/429 and temporary 5xx
  responses defer work to a later scheduler run, respecting `Retry-After`.
- Redis prevents concurrent reconciliation of the same call. Unresolved checks
  back off from 30 seconds to approximately 15 minutes, with jitter. Retries
  continue while unresolved; one diagnostic alert is written after eight
  attempts. Callback processing can still finish a call immediately. Redis loss
  resets cooldowns, never call outcomes.
- Provider GET requests share a per-site, per-account budget of 120 per minute.
  `vobiz_provider_reads_per_minute` in site configuration overrides that limit.
  Exceeding it delays reads; it does not place another call.
- A fresh heartbeat from the owning browser window with healthy active media
  defers periodic CDR reads. Cancellation, termination, stale presence or
  reconnecting audio removes that exemption. UI verification queues CDR work.
- CDR lookup first uses the exact UUID endpoint, then at most two filtered
  search pages. A matching provider identifier and final evidence are required.
  Network requests run outside row locks; the latest call is locked afterward
  so an intervening callback or changed UUID cannot be overwritten.
- Every five minutes the CDR scheduler considers up to 100 terminal calls,
  reserving capacity for the older backlog, in batches of five. Failed reads
  rotate through the backlog. Call outcomes remain separate from agent-leg
  billing: final customer failure and explicit B-leg outcomes are preserved.
- The browser checks microphone access and registration before creating an
  outbound call. SDK diagnostics retain at most 65,536 characters; unavailable,
  corrupt or full local storage does not interrupt call event handling.

Deploy both apps together, build their assets, synchronize scheduler hooks with
the site's normal migration procedure, clear caches and reload application
workers. The bundled SDK URL includes a new cache version. External custom SDK
URLs must receive their own storage fix. Agents should refresh existing tabs.

Provider outages and agents being offline still require operational attention.
Keep provider callbacks reachable and agent ownership/fallback mappings valid.
These changes reduce unnecessary requests and preserve pending/failed states;
they do not guarantee that an unavailable provider or offline agent can answer.
