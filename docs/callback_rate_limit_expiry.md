# Vobiz callback rate-limit expiry repair

## Problem and scope

SRIAAS server evidence showed an answer callback returning HTTP 429 through
`services.callback_limits` and Frappe's method limiter. A subsequent console
inspection reported limit 6000, count 16654 and TTL -1. TTL -1 means the counter
exists without an expiry; that count is not a measurement of one minute's traffic.

The inspected framework implementation performs GET, conditional SETEX and INCR
separately. Expiry between GET and INCR can recreate a counter without an expiry.
The real Redis test reproduces this sequence deterministically. This demonstrates
a code defect; it does not establish that this was the only historical source of
the production key's missing expiry.

The application wrapper now increments the existing raw counter and initializes
or repairs its expiry in one Redis Lua operation. It preserves:

- The site's `frappe.cache.make_key` namespace and `rl:{cmd}:{request_ip}` key.
- Runtime answer, event and hangup limits and a 60-second window.
- Counting of rejected requests and the existing RateLimitExceededError/HTTP 429 behavior.
- Authentication in each handler and protection against counting nested callbacks twice.
- Healthy expiry, including subsecond expiry. Requests do not slide the window forward.

Redis errors propagate: the wrapper does not silently disable protection. An
existing over-limit counter retains its count and remains blocked until its new
60-second expiry. It is not zeroed or deleted.

The shared wrapper covers the decorated callbacks in `vobiz_system_call.api.webrtc`
and `vobiz_click_to_call.api.inbound` / `.webhook`. The migration has an exact
17-command allowlist for these routes. Other method limiters, provider-read
budgets, Frappe's site-wide time-based limiter and Vobiz's upstream quotas are
outside this change. No Frappe framework files or callback authentication are changed.

## Existing keys and diagnostics

The registered patch
`vobiz_click_to_call.patches.v1_0.repair_callback_counter_expiry` uses SCAN and
conditionally attaches expiry to matching non-expiring keys for the selected
site. Each TTL check and repair is atomic. It does not refresh healthy timers,
reset counts, touch other sites, or clear sessions/cache. It can be rerun safely.

The wrapper also repairs a missing expiry when that key receives its next request.
The repair emits `callback_counter_expiry_repaired`; rejected requests emit
`callback_rate_limited`. Both include endpoint, handler, source IP, configured
limit, counter, remaining TTL in milliseconds and a UTC timestamp. Available
`call_log` and `CallUUID` identifiers are allowlisted and bounded. Request URLs,
tokens, headers and payloads are not logged by this change. Logs use the existing
`vobiz_callbacks` logger rather than inserting database Error Log documents.

## Validation

From the bench directory:

```sh
env/bin/python -m unittest \
  vobiz_click_to_call.tests.test_callback_limits \
  vobiz_click_to_call.tests.test_callback_limits_redis -v
```

The integration tests require `redis-server`. They start their own temporary
Unix-socket Redis with TCP and persistence disabled. They do not connect to a
site's Redis. Ensure these tests run rather than skip in the release environment.

Coverage includes creation, boundary rejection, persistent-key recovery, expiry
and next-window recovery, concurrent requests at expiry, counter accuracy,
unchanged healthy TTL, site/IP/endpoint isolation, nested authentication,
exception cleanup, Redis failure behavior, safe diagnostics and idempotent repair.

## Deployment

This document is a runbook, not a record of a production deployment.

1. Record the deployed app revisions and effective configuration in the intended
   site context. Preserve local work and deploy the reviewed application revision
   through the normal deployment process. This change itself has no frontend assets.
2. Run the site's normal migration to execute the registered repair:

   ```sh
   bench --site sriaas.butest.tech migrate
   ```

3. Restart/reload all web and background workers using the deployment platform's
   normal procedure. Old workers must not continue using the non-atomic limiter.
   On a bench-managed deployment this normally includes `bench restart`.
4. After all workers run the new code, optionally repeat the targeted repair to
   catch a key recreated by an old worker during rollout:

   ```sh
   bench --site sriaas.butest.tech execute \
     vobiz_click_to_call.services.callback_limits.repair_callback_counter_expiry
   ```

5. Verify the effective `vobiz_answer_requests_per_minute`,
   `vobiz_event_requests_per_minute` and `vobiz_hangup_requests_per_minute` across
   workers. This patch does not raise their values.
6. Observe callback counters over multiple windows and representative traffic.
   Existing active counters should have PTTL between 0 and 60000; PTTL -2 means
   the key has expired or is absent. Persistent PTTL -1 is an investigation signal.
7. With authorized test numbers, compare callback HTTP results, exact provider
   call-leg records and ERP status for multiple agents. Inspect both Vobiz source
   IPs and other source IPs seen in real logs. One successful call is insufficient.
   A genuine burst over the configured limit should still receive HTTP 429.

Use raw Redis operations on `frappe.cache.make_key(...)` output. Some Frappe
helpers add the namespace themselves; for example, `cache.exists(key)` would
prefix an already-qualified key again. `cache.pttl(key)`, `cache.get(key)` and
`cache.execute_command("EXISTS", key)` address the qualified counter directly.

## Rollback and remaining investigation

Keep the preceding deployed revision recorded. If rollback is necessary, deploy
that revision and restart all workers. The key format and values are unchanged,
so no counter conversion or reverse database migration is required. A restored
legacy limiter can recreate the original defect; the one-time expiry repair is
not a permanent substitute for the atomic implementation. Never flush Redis or
disable callback authentication/rate limiting as a rollback measure.

This fix does not change call outcomes. `Answer XML / Rejected`, action-URL
failures and customer `No Answer` records must still be correlated with server
HTTP logs and exact call UUIDs. Nor does it modify the independent temporary
Complete Call Server Script. Production callback throughput, deployed revisions,
successful rollout and provider-side recovery require production verification.
