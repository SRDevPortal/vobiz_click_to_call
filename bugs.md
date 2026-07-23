# Avoidable API Calls Audit

## Purpose

This document identifies API calls in `vobiz_click_to_call` that are unnecessary, duplicated, or more expensive than required. It focuses on calls made repeatedly during normal Desk and Vobiz Agent Console usage. User-triggered mutations, provider calls required to start or stop a phone call, and pagination requests are not treated as unnecessary.

## Executive summary

The highest-cost problem is the Agent Console polling design. While the console is visible, it requests as many as 500 queue records every five seconds. During an active workdesk call, that response is immediately followed by another API request for call status, even though the first response already contains the active call. The globally loaded availability script also runs its own availability polling and activity heartbeat alongside the console heartbeat.

At steady state, one visible Agent Console tab can make approximately:

| Source | Frequency | Approximate calls/minute |
| --- | ---: | ---: |
| Full console reload | Every 5 seconds | 12 |
| Separate call-status lookup during an active workdesk call | Every console reload | Up to 12 |
| Console heartbeat | Every 25 seconds | 2.4 |
| Global activity heartbeat | Every 30 seconds | 2 |
| Global availability refresh | Every 60 seconds | 1 |

This is up to roughly **29 calls per minute per active console tab**, before user actions and WhatsApp requests. The actual number depends on whether a call is active and whether idle/availability tracking is enabled.

## Finding 1: full 500-row console reload every five seconds

**Severity:** High  
**Location:** `vobiz_click_to_call/vobiz_click_to_call/page/vobiz_agent_console/vobiz_agent_console.js`, lines 530-596

`start_polling()` invokes `load()` every five seconds:

```javascript
this.poller = setInterval(() => this.load(), 5000);
```

Every execution calls `get_agent_console_data` with a limit of 500:

```javascript
frappe.call('vobiz_click_to_call.api.console.get_agent_console_data', {
    limit: 500,
    // filters omitted
});
```

The backend then rebuilds dynamic data including call capability, active call, and the full lead/patient queue. See `vobiz_click_to_call/api/console.py`, lines 250-283.

### Why it is avoidable

Most of the queue does not change every five seconds. The application already binds realtime handlers, so indiscriminately retrieving 500 rows is much more work than checking only volatile state or applying a specific realtime update.

At the current interval, each open console creates 720 full reloads per hour. Ten simultaneously open consoles can therefore generate 7,200 large API requests per hour.

### Recommended change

Use one or more of the following:

1. Refresh the queue in response to Frappe realtime events for record creation and relevant field changes.
2. Poll a small delta/status endpoint containing only `active_call`, availability, queue revision, and changed record identifiers.
3. Reload the full queue only when its revision changes, filters change, or the user explicitly refreshes.
4. If polling must remain, increase the interval and stop it while the document is hidden. The current visibility check prevents work inside `load()`, but the timer itself continues firing.
5. Reduce the default page size and use pagination instead of requesting 500 records on every refresh.

## Finding 2: duplicate call-status request after the console response

**Severity:** High while a call is active  
**Location:** `vobiz_agent_console.js`, lines 539-564 and 2095-2120

`get_agent_console_data` already returns `active_call`. The success handler saves it to `this.state.active_call`, but then calls `refresh_workdesk_live_call()`:

```javascript
this.state.active_call = data.active_call || null;
// ...
this.refresh_workdesk_live_call();
```

When `workdesk_live_call_log` is set, `refresh_workdesk_live_call()` immediately makes another request:

```javascript
frappe.call({
    method: 'vobiz_click_to_call.api.call.get_call_status',
    args: { call_log: callLog }
});
```

### Why it is avoidable

The full console response has just fetched `_active_call()` on the server. For the currently active call, the second request asks for substantially overlapping state immediately afterward. During a call, this can double the five-second polling traffic from 12 to 24 calls per minute.

### Recommended change

Render the workdesk call from `data.active_call` when its name matches `workdesk_live_call_log`. Call `get_call_status` only when:

- the full response does not contain the tracked call;
- a targeted refresh is required without reloading the console; or
- the call is awaiting terminal state and realtime events are unavailable.

The best long-term design is a targeted call-status realtime event, with low-frequency polling only as a fallback.

## Finding 3: overlapping presence and availability heartbeats

**Severity:** Medium  
**Locations:**

- `vobiz_agent_console.js`, lines 608-646
- `vobiz_click_to_call/public/js/availability.js`, lines 550-598

The Agent Console sends `heartbeat_agent_console` every 25 seconds. At the same time, the globally loaded availability script sends `record_agent_activity` every 30 seconds while the user is active.

Both calls maintain closely related agent presence/activity state. On the Agent Console page, both systems are active and produce approximately 4.4 heartbeat calls per minute combined.

### Why it may be avoidable

The console heartbeat already proves that the console tab is open, and the global activity tracker already observes user input and visibility. Maintaining two independent periodic writes/checks creates duplicated network and database/cache work.

### Recommended change

Use one presence endpoint with a payload describing:

```json
{
  "tab_id": "...",
  "route": "vobiz-agent-console",
  "visible": true,
  "last_activity_at": "..."
}
```

The server can use the same heartbeat to extend the console session and record activity. If the two endpoints must remain for compatibility, suppress the global `record_agent_activity` timer on the Agent Console route and let the console heartbeat include recent-activity information.

This change requires confirming that idle auto-offline behavior still distinguishes an open but unattended tab from real user activity.

## Finding 4: redundant global availability polling on the Agent Console

**Severity:** Medium  
**Location:** `vobiz_click_to_call/public/js/availability.js`, lines 46-74 and 493-501

The globally loaded script calls `get_my_availability` on initialization and every 60 seconds. The Agent Console separately calls `get_agent_console_data` every five seconds, and that response already includes:

```python
"availability": get_call_capability()
```

The console renders this returned availability state with `render_availability()`.

### Why it is avoidable

On the Agent Console route, availability is already refreshed twelve times per minute through the main console endpoint. The additional once-per-minute `get_my_availability` request does not provide fresher console state.

### Recommended change

Disable the global availability refresh timer while the Agent Console owns availability state. Dispatch the existing `vobiz_availability_changed` event—or update the shared availability control directly—using availability returned by `get_agent_console_data`.

Outside the console, the global 60-second refresh remains useful and should not be removed.

## Finding 5: static configuration is returned on every console poll

**Severity:** Medium  
**Location:** `vobiz_click_to_call/api/console.py`, lines 267-305

Every `get_agent_console_data` response includes:

- queue metadata;
- disposition options;
- patient follow-up status options in the cached static context;
- the AI-disposition enabled flag.

The backend caches this static context, which reduces database work, but the values are still serialized and transferred with every five-second response. The frontend overwrites the same state on every poll.

### Why it is avoidable

These values change much less often than availability, active-call state, or queue records. Caching on the server does not eliminate the HTTP response size or frontend processing.

### Recommended change

Return static context only on initial load or when a `static_context_version` changes. A clean contract would split the API into:

- `get_agent_console_bootstrap`: permissions, metadata, options, configuration;
- `get_agent_console_state`: availability, active call, queue revision/delta.

Alternatively, accept a client-known version and omit static fields when it matches.

## Finding 6: repeated reference-context requests have no short-lived cache

**Severity:** Medium  
**Location:** `vobiz_agent_console.js`, including lines 787, 854, 1384, 1657, 1734, 3125, 3496, and 3684

`get_reference_context` is called from many paths:

- selecting a queue row;
- opening a call detail;
- opening WhatsApp;
- restoring the workdesk;
- handling routed calls;
- preparing the post-call disposition dialog.

The application stores only the last result in `this.state.context`. It does not key that state by DocType/name or reuse a recent response. Consequently, selecting a row and immediately opening its call or WhatsApp action can fetch the same `lite` context again.

There is protection against some exact simultaneous actions through `detail_loading_key`, but not against sequential calls for the same record.

### Recommended change

Add an in-memory context cache keyed by:

```text
reference_doctype::reference_name::lite
```

Use a short time-to-live such as 15-30 seconds and cache the in-flight Promise as well as the resolved value. Invalidate the entry after any mutation to the reference, disposition, note, status, or related call state.

This avoids repeated reads while preserving freshness.

## Finding 7: cancellation performs an immediate status read and then a full reload

**Severity:** Low to medium  
**Location:** `vobiz_agent_console.js`, lines 3626-3648

The cancellation sequence is:

1. call `cancel_call`;
2. call `get_call_status`;
3. call the full `load()` endpoint.

This creates three sequential API requests for one action.

### Why part of it is avoidable

`cancel_call` controls the mutation and can return the updated call representation. If it returns the fields currently obtained from `get_call_status`, the immediate second request can be removed. The subsequent full reload can then be replaced by a local update or delayed until the normal polling/realtime cycle.

### Recommended change

Return the normalized updated call from `cancel_call`. Update `active_call`, `workdesk_live_call`, and the matching queue row locally. Keep one background reconciliation request only if required for provider/webhook races.

## Calls reviewed but not classified as unnecessary

The following calls are currently justified:

- Vobiz provider calls to make, retrieve, or hang up a call;
- OpenAI analysis calls when AI disposition is explicitly enabled;
- WhatsApp message pagination through `before` cursors;
- mutation endpoints for notes, statuses, dispositions, assignments, and messages;
- keepalive/offline requests during page exit;
- a fallback status poll when realtime delivery cannot be guaranteed.

## Recommended implementation order

1. Remove the duplicate `get_call_status` request after a successful console reload.
2. Replace the five-second full queue reload with a lightweight state/delta request or realtime updates.
3. Prevent global availability polling on the Agent Console route.
4. Consolidate console and activity heartbeats after validating idle auto-offline semantics.
5. Split static bootstrap data from volatile console state.
6. Add a short-lived, mutation-aware reference-context cache.
7. Return updated call state directly from `cancel_call`.

## Expected result

With realtime or lightweight state polling, a visible idle console should require only a presence heartbeat plus occasional reconciliation. During an active call, it should not need both a full 500-row reload and a separate call-status request every five seconds. The target should be a small number of lightweight calls per minute rather than up to roughly 29 mixed calls per minute per tab.
