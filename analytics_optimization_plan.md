# Vobiz Analytics and Database Optimization Plan

## 1. Objective

This runbook describes how to reduce database load and response time caused by the Vobiz Agent Analytics dashboard, Agent Console polling, attendance tracking, CRM phone lookup, Patient Encounter queries, and Chat Conversation writes.

The production slow-query sample covered approximately 11 hours and 23 minutes and contained:

- 12,136 slow queries;
- approximately 43,576 seconds of accumulated query time;
- approximately 5.54 billion rows examined;
- up to roughly 29 application calls per minute from one active Agent Console tab.

The target is to reduce rows examined by at least 90%, eliminate redundant analytics computation, and keep common dashboard requests below one second for normal date ranges.

## 2. Primary bottlenecks

| Priority | Area | Observed impact | Principal cause |
| --- | --- | --- | --- |
| P0 | Attendance queries | ~1.29B rows examined | Missing composite indexes and frequent heartbeats |
| P0 | CRM phone lookup | ~2.05B rows examined | Leading-wildcard searches on large Lead fields |
| P0 | Patient Encounter queries | ~1.47B rows examined | Broad selects, joins, missing filter indexes |
| P0 | Chat Conversation updates | ~10.47s average | Concurrent writes and oversized transactions |
| P0 | Agent Console refresh | Every five seconds | Full queue retrieval plus redundant status call |
| P1 | Analytics API | Recomputed for pagination | One endpoint performs every analytics workload |
| P1 | Naming series | Up to 50 seconds | Contention on a shared `tabSeries` row |

## 3. Safety rules

Before applying any schema change:

1. Take a verified database backup.
2. Record the MariaDB version and table sizes.
3. Test the exact DDL against a recent production copy.
4. Capture `SHOW CREATE TABLE` for every affected table.
5. Run `EXPLAIN` before and after each index change.
6. Do not guess whether a DDL operation is online. Confirm support for the installed MariaDB version.
7. Do not hold application transactions open while calling Vobiz, WhatsApp, OpenAI, or another external service.
8. Apply one optimization group at a time and measure it before proceeding.

## 4. Workloads to pause during maintenance

Temporarily block or pause:

- access to the Vobiz Agent Analytics page;
- Agent Console five-second polling;
- `heartbeat_agent_console` and `record_agent_activity` while changing attendance indexes;
- analytics exports and scheduled reports;
- AI lead-scoring workers while investigating Chat Conversation contention;
- other bulk jobs reading CRM Lead, Patient Encounter, or Chat tables.

Allow active calls to finish before pausing workers.

Do not disable these services unless the site is in a full maintenance window:

- inbound Vobiz webhooks;
- call-status and CDR callbacks;
- call start/hangup endpoints;
- recording callbacks;
- WhatsApp inbound webhooks;
- MariaDB, Redis, or unrelated Frappe web workers.

Recommended maintenance sequence:

```text
Block Analytics page
  -> close or pause Agent Console tabs
  -> pause scheduled and bulk workers
  -> wait for active calls to finish
  -> back up and apply one schema change
  -> ANALYZE TABLE and verify EXPLAIN
  -> resume workers gradually
  -> monitor errors, latency, locks and row scans
  -> restore Console and Analytics access
```

## 5. Phase 0: measurement and containment

### 5.1 Record a baseline

For each important endpoint, capture:

- request count per minute;
- p50, p95 and p99 duration;
- database query count;
- rows examined;
- response payload size;
- error rate;
- concurrent requests;
- cache hit rate.

Measure these endpoints separately:

```text
vobiz_click_to_call.api.console.get_analytics
vobiz_click_to_call.api.console.get_agent_console_data
vobiz_click_to_call.api.console.get_reference_context
vobiz_click_to_call.api.call.get_call_status
vobiz_click_to_call.api.call.get_my_availability
vobiz_click_to_call.api.console.heartbeat_agent_console
vobiz_click_to_call.api.console.record_agent_activity
```

### 5.2 Add request correlation

Log a request ID with:

- endpoint;
- authenticated user;
- filter hash;
- elapsed time;
- SQL-query count;
- returned row count;
- response bytes.

For Chat Conversation processing, also log conversation ID, message ID, background job ID, transaction duration, and external-call duration.

### 5.3 Immediate containment

Until code changes are deployed:

- restrict Analytics to a reasonable maximum date range;
- prevent repeated clicks while a request is in flight;
- cancel or ignore stale filter requests;
- prevent auto-refresh on Analytics;
- increase the Agent Console reconciliation interval if realtime is operational;
- stop polling when the browser document is hidden;
- cap queue and report page sizes.

## 6. Phase 1: attendance optimization

### 6.1 Existing problem

Attendance queries filter by combinations of:

- `agent_user`;
- `tab_id`;
- `status`;
- `shift_date`;
- `source`;
- `modified` or `creation` ordering.

The slow log shows individual executions examining up to roughly 486,000 rows.

### 6.2 Candidate indexes

Validate these candidates on a production-like copy:

```sql
ALTER TABLE `tabVobiz Agent Attendance Log`
  ADD INDEX `idx_vobiz_attendance_user_open_day_modified`
  (`agent_user`, `status`, `shift_date`, `modified`);
```

```sql
ALTER TABLE `tabVobiz Agent Attendance Log`
  ADD INDEX `idx_vobiz_attendance_user_tab_open_day_creation`
  (`agent_user`, `tab_id`, `status`, `shift_date`, `creation`);
```

For daily availability reports, evaluate:

```sql
ALTER TABLE `tabVobiz Agent Attendance Log`
  ADD INDEX `idx_vobiz_attendance_user_day_source_online`
  (`agent_user`, `shift_date`, `source`, `online_from`);
```

Do not deploy all candidate indexes automatically. Every index increases write and storage cost. Keep only indexes proven useful by `EXPLAIN` and production metrics.

### 6.3 Rewrite non-sargable filtering

Avoid:

```sql
COALESCE(source, '') != 'Availability'
```

Make `source` non-null for new and existing records, then use explicit values. Where possible, query the permitted sources with `IN (...)` instead of a negative condition.

### 6.4 Avoid querying attendance on every heartbeat

Cache the open attendance record name by user, tab and shift date:

```text
vobiz:attendance:{shift_date}:{user}:{tab_id} -> attendance_record_name
```

On a heartbeat:

1. read the cached record name;
2. update only `last_seen_at`;
3. query for an open session only on a cache miss;
4. expire the cache after the session TTL;
5. remove it when closing the session.

This converts repeated discovery queries into direct primary-key updates.

### 6.5 Consolidate presence calls

On the Agent Console route, combine console presence and activity tracking into one heartbeat payload:

```json
{
  "tab_id": "browser-tab-id",
  "route": "vobiz-agent-console",
  "visible": true,
  "last_activity_at": "2026-07-21T10:15:00"
}
```

The server should use this to extend the console session and update activity state. Preserve the distinction between a visible-but-idle tab and genuine user activity.

## 7. Phase 2: CRM phone lookup optimization

### 7.1 Existing problem

Queries use leading-wildcard searches:

```sql
WHERE mobile_no LIKE '%%7358329628%%'
```

A normal index cannot optimize a leading wildcard. Adding an index to `mobile_no` or `phone` alone will not solve this problem.

### 7.2 Preferred data model

Create a normalized phone identity table:

```text
Vobiz Phone Identity
  normalized_number
  last_10_digits
  reference_doctype
  reference_name
  source_field
  is_primary
  modified
```

Recommended indexes:

```text
UNIQUE(reference_doctype, reference_name, source_field, normalized_number)
INDEX(normalized_number)
INDEX(last_10_digits)
INDEX(reference_doctype, reference_name)
```

Lookup becomes:

```sql
SELECT reference_doctype, reference_name
FROM `tabVobiz Phone Identity`
WHERE last_10_digits = '7358329628'
LIMIT 20;
```

### 7.3 Transitional option

If a separate table cannot be introduced immediately:

1. add normalized `mobile_last10` and `phone_last10` fields;
2. backfill them in small batches;
3. update them in validation hooks;
4. index them;
5. switch lookup code to equality;
6. remove wildcard lookup after validation.

### 7.4 Normalization rules

- remove spaces, punctuation and formatting;
- retain country code in a canonical full-number field;
- store a last-ten-digits key only where that matching rule is valid;
- do not treat two numbers as identical solely by suffix when country rules make that ambiguous;
- keep normalization consistent across CRM Lead, Contact, Patient, Chat Contact and call logs.

## 8. Phase 3: split and cache Analytics services

### 8.1 Existing problem

The frontend uses one endpoint for every operation:

```text
vobiz_click_to_call.api.console.get_analytics
```

That endpoint can compute:

- summary KPIs;
- filtered summary;
- status breakdown;
- outcome breakdown;
- daily chart;
- per-agent aggregates;
- filter options;
- attendance snapshots;
- paginated call rows.

Opening or paging a call list can therefore recompute charts and summaries that did not change.

### 8.2 Proposed API contracts

#### `get_analytics_bootstrap`

Return infrequently changing filter metadata:

```json
{
  "queue_sources": [],
  "agent_options": [],
  "lead_owner_options": [],
  "team_options": [],
  "department_options": [],
  "permissions": {}
}
```

Cache per user and permission scope. Invalidate when mappings, teams or permissions change.

#### `get_analytics_summary`

Return only:

```json
{
  "summary": {},
  "filtered_summary": {},
  "status_breakdown": [],
  "outcome_breakdown": [],
  "daily": [],
  "agents": []
}
```

#### `get_analytics_calls`

Return only paginated rows:

```json
{
  "calls": [],
  "next_cursor": null,
  "has_more": false,
  "matching_call_count": 0
}
```

#### `get_agent_attendance_analytics`

Return attendance records and availability durations separately. Do not recalculate attendance when the UI only changes call-status pagination.

### 8.3 Cache design

Cache immutable summary results using a stable key:

```text
analytics:v2:{permission_scope_hash}:{filter_hash}:{data_revision}
```

The filter hash must include:

- date range;
- queue source;
- status;
- agents;
- lead owners;
- teams;
- department;
- caller permission scope.

Never share a cached result across users with different data visibility.

Suggested TTLs:

| Data | TTL |
| --- | ---: |
| Filter/bootstrap options | 5-15 minutes |
| Historical summary ending before today | 30-60 minutes |
| Summary including today | 30-60 seconds |
| Attendance snapshot | 10-30 seconds |
| Individual call page | 10-30 seconds, or no cache |

Prefer revision-based invalidation over relying only on TTL.

### 8.4 Use cursor pagination

Replace large offsets with a stable cursor based on indexed ordering fields, for example:

```text
(creation, name) < (last_creation, last_name)
ORDER BY creation DESC, name DESC
LIMIT 50
```

Large `OFFSET` values force the database to scan and discard preceding rows.

### 8.5 Required Call Log indexes

Derive final indexes from actual SQL and `EXPLAIN`. Likely candidates are:

```text
(creation, reference_doctype)
(user, creation)
(reference_doctype, creation)
(reference_doctype, reference_name, creation)
(status, creation)
```

Do not create every permutation. Start with the highest-frequency filter/order combinations and confirm selectivity.

## 9. Phase 4: Agent Console request reduction

### 9.1 Replace full polling

The current console requests up to 500 queue records every five seconds. Replace it with:

1. an initial bootstrap/queue page;
2. realtime events for call, assignment and queue changes;
3. a lightweight reconciliation endpoint every 30-60 seconds;
4. a full reload only when the server revision differs.

Example reconciliation response:

```json
{
  "revision": 18422,
  "active_call": {},
  "availability": {},
  "changed": ["CRM-LEAD-0001"],
  "removed": [],
  "server_time": "2026-07-21T10:15:00"
}
```

### 9.2 Remove duplicate call-status retrieval

If `get_agent_console_data` or the reconciliation response contains the tracked active call, do not immediately call `get_call_status` for the same call.

Use a targeted call-status request only when:

- realtime delivery may have been missed;
- the active call is absent from the latest response;
- a terminal state is overdue.

### 9.3 Stop overlapping availability polling

On the Agent Console route, use availability returned by console state. Disable the global once-per-minute `get_my_availability` request there. Keep it on other Desk routes.

### 9.4 Cache reference context

Cache both the in-flight Promise and resolved result by:

```text
reference_doctype::reference_name::lite
```

Use a 15-30 second TTL. Invalidate after notes, dispositions, status changes, assignments, calls or other reference mutations.

## 10. Phase 5: Patient Encounter optimization

### 10.1 Capture complete SQL

The slow log shows Patient Encounter queries examining approximately one million rows per execution, but the displayed fingerprints are truncated. Capture the complete highest-frequency queries and run:

```sql
EXPLAIN FORMAT=JSON <query>;
```

Record:

- chosen indexes;
- estimated rows;
- actual rows returned;
- temporary-table usage;
- filesort usage;
- join order;
- dependent subqueries.

### 10.2 Reduce selected fields

Queue and analytics list endpoints should return only rendered fields. Do not retrieve all standard and custom Patient Encounter fields.

### 10.3 Prevent child-table join multiplication

Payment and order-item child tables should be pre-aggregated by parent before joining:

```sql
LEFT JOIN (
  SELECT parent, SUM(amount) AS total_amount
  FROM child_table
  GROUP BY parent
) totals ON totals.parent = encounter.name
```

Alternatively, maintain summary fields on Patient Encounter when child rows change.

### 10.4 Optimize assignment counts

Replace broad `IN (SELECT ...)` queries over Patient and ToDo with a selective join or maintained aggregate. Evaluate:

```text
tabToDo(reference_name, status, allocated_to)
```

and composite Patient indexes matching actual department, follow-up day, status and ID filters.

### 10.5 Enforce pagination

- use cursor pagination;
- cap page size;
- avoid exporting synchronously through a web request;
- send large exports to a background job using a read replica where available.

## 11. Phase 6: Chat Conversation write contention

### 11.1 Consolidate writes

For one inbound message, collect all conversation changes and issue one update rather than separate updates for:

- message timestamps;
- messaging-window state;
- CTWA attribution;
- CRM linkage;
- AI score, temperature and language;
- unread count and message preview.

### 11.2 Serialize per conversation

Use a per-conversation job key or short distributed lock:

```text
chat-conversation:{conversation_id}
```

Different conversations can process concurrently; events for the same conversation must retain order.

Locks must have a bounded timeout and guaranteed release. Do not hold the lock during external API calls if the workflow can split fetch and commit phases safely.

### 11.3 Keep transactions short

Correct sequence:

```text
read required state
  -> commit/close transaction if needed
  -> call external service
  -> begin short transaction
  -> validate current version
  -> update changed fields once
  -> commit immediately
```

### 11.4 Deduplicate AI work

- do not enqueue scoring twice for the same message/version;
- store a content or transcript hash;
- skip scoring when the hash has already succeeded;
- coalesce rapid messages into one scoring job when acceptable;
- update only when the result differs.

### 11.5 Diagnose infrastructure waits

Because single-row primary-key updates reached 50 seconds while reported SQL lock time remained low, inspect:

- `SHOW ENGINE INNODB STATUS`;
- performance-schema transaction and wait tables;
- buffer-pool hit rate;
- disk latency and IOPS;
- binary-log and redo-log flush latency;
- long-running open transactions;
- connection saturation;
- application hooks executed around saves.

## 12. Phase 7: naming-series contention

Identify which high-volume DocTypes use the contended `tabSeries` keys.

For records that do not require sequential business numbering, prefer:

- hash names;
- UUIDs;
- database auto-increment identifiers.

If sequential numbering is mandatory, allocate ranges to workers and commit series allocation immediately. Never retain a `tabSeries` lock while performing unrelated work.

## 13. Remove or retire obsolete API code

`vobiz_click_to_call.api.reports.get_dashboard_summary` is not referenced by the current frontend. Confirm external consumers through access logs. If none exist:

1. mark it deprecated;
2. monitor usage for a release cycle;
3. remove it or redirect callers to the optimized summary endpoint.

Do not leave two independent analytics implementations that can diverge or encourage inefficient usage.

## 14. Verification checklist

After each deployment:

- [ ] `EXPLAIN` selects the intended index.
- [ ] Rows examined per attendance lookup are near the returned-row count.
- [ ] CRM phone lookup uses equality on a normalized indexed value.
- [ ] Analytics pagination does not recompute summary charts.
- [ ] Agent Console does not fetch 500 rows every five seconds.
- [ ] A console state response is not followed by duplicate call-status retrieval.
- [ ] The Agent Console does not run a redundant global availability poll.
- [ ] Reference context is reused briefly and invalidated after mutations.
- [ ] Chat processing performs one consolidated update per logical event.
- [ ] External calls occur outside database transactions.
- [ ] No new deadlocks, stale availability state, missed calls or permission leaks appear.
- [ ] Cache keys include the complete permission scope.
- [ ] Slow-query volume and database CPU fall after rollout.

## 15. Performance acceptance targets

| Metric | Target |
| --- | ---: |
| Analytics summary p95, normal range | < 1.0 second |
| Analytics call-page p95 | < 500 ms |
| Console reconciliation p95 | < 300 ms |
| Attendance heartbeat p95 | < 150 ms |
| Indexed phone lookup p95 | < 100 ms |
| Rows examined per phone lookup | < 50 |
| Rows examined per attendance lookup | < 20 |
| Duplicate status calls after console refresh | 0 |
| Full console queue refresh frequency | Initial load or revision change only |
| Single-row Chat Conversation update p95 | < 200 ms |
| Slow queries over two seconds | Reduce by at least 90% |

## 16. Rollout order

Use this order to gain the largest benefit with controlled risk:

1. Add instrumentation and capture baseline measurements.
2. Add and validate attendance indexes.
3. Normalize phone lookup and remove leading wildcards.
4. Remove duplicate Agent Console status and availability calls.
5. Split Analytics summary, calls, bootstrap and attendance APIs.
6. Add permission-aware summary caching and cursor pagination.
7. Replace full console polling with realtime plus lightweight reconciliation.
8. Optimize complete Patient Encounter queries using `EXPLAIN`.
9. Consolidate and serialize Chat Conversation writes.
10. Remove naming-series contention from high-volume DocTypes.
11. Retire unused analytics API code after confirming no external consumers.

## 17. Rollback plan

For every phase:

1. retain the previous endpoint or code path behind a feature flag;
2. deploy schema additions before code that depends on them;
3. avoid dropping old fields or indexes in the same release;
4. monitor one agent/team cohort before global rollout;
5. revert application routing first if errors appear;
6. remove new indexes only after confirming rollback traffic no longer needs them;
7. restore paused workers gradually to avoid a queued-job surge.

Schema additions are usually easier to roll back operationally by leaving them unused. Index removal should be performed in a separate maintenance window, not as an immediate reaction during an incident.
