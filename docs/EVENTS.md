# Task events

The JARVIS command center is built on real, persisted **task events**: every
agent state, timeline entry and network transition the UI shows is backed by an
event a backend worker actually emitted. There is no simulated progress.

## Schema (`server/app/schemas/events.py`)

Every event is a versioned `TaskEvent`:

| field | meaning |
| --- | --- |
| `v` | schema version (currently `1`) |
| `event_id` | unique id (a `uuid4`), also the Mongo `_id` |
| `task_id` | the execution this event belongs to |
| `seq` | monotonically increasing integer, unique within the task |
| `ts` | UTC timestamp (normalised to aware UTC on construction) |
| `type` | one of the closed set below |
| `agent_id` | the registry id (`master`/`negotiation`/`collections`/`lead_followup`/`chat`) or `null` |
| `payload` | validated, bounded metadata |

### Event types (closed set)

`task.created`, `task.started`, `task.completed`, `task.failed`,
`task.cancelled`, `agent.queued`, `agent.started`, `agent.progress`,
`agent.completed`, `agent.failed`, `tool.started`, `tool.completed`,
`erp.sync_started`, `erp.sync_completed`, `erp.sync_failed`.

### Payload bounds

`payload` is validated so an event that is broadcast to clients can never carry
secrets or bulk PII: keys containing `token`, `secret`, `password`,
`credential`, `api_key`, `authorization`, `refresh` are rejected; string fields,
sequence lengths, nesting depth and the total serialised size are all capped.
Customer names, contact details, prompts and raw tool results are never placed
in a payload — only identifiers, counts, durations, statuses and outcomes.

The `token` rule matches substrings, so the *billing counts* are allowlisted
explicitly (`total_tokens`, `input_tokens`, `output_tokens`, `prompt_tokens`,
`completion_tokens`, `reasoning_tokens` in
`ALLOWED_SENSITIVE_SUBSTRING_KEYS`); a token **value** is still rejected, and a
key that merely looks like a count (`total_tokens_but_really_a_secret`) is too.
Before that list existed, every `task.completed` was rejected for carrying
`total_tokens` and silently vanished, so no task ever reached a terminal state.
A rejected event is now counted (`EventService.rejected`) and logged loudly with
its payload **keys** and a sanitised message - never its values, which
Pydantic would otherwise echo back as `input_value=...`.

## Task status reconstruction

`GET /api/tasks` collapses each task's **whole** event set, not just its newest
row: the newest terminal event wins (`task.completed/failed/cancelled` and
`erp.sync_completed/failed`). With no terminal event a task is reported
`running` only while it is recent (under `STALE_TASK_SECONDS`); an older run
that simply stopped is `unknown`, which the UI labels "Outcome not recorded"
rather than presenting dead work as live. The ERP refresh emits
`task.created` → `erp.sync_started` → (`erp.sync_completed` |
`erp.sync_failed`) → the matching `task.*`, and the terminal pair is emitted
only **after** the refresh call has actually returned.

Every row also carries what its own documents prove and nothing more:
`created_at`/`updated_at` (first and last recorded moment), `duration_ms` (only
once the task reached an ending - an unfinished run reports `null`, never an
invented span), `agents` (the distinct `agent_id`s that took part) and
`error_kind` for a failure. `error_kind` is the exception **type name**
(`ConnectionError`, `ERPError`), stored at emit time; the message is never kept,
because an exception string can echo the record it failed on.

## Chat evidence

A successful record lookup sends `event: tool_result` on the chat stream with
the aggregates Python computed for that call - record counts, completeness, a
money total and the query window. Aggregates only: no row, customer name or
contact crosses this boundary. The paged subset of the same facts is also merged
into the durable `tool.completed` payload, so a client that missed the live
frame still reconstructs the evidence from the replay. A total the tool computed
over *all* matched records says so (`sum_amount_covers`); the UI may not present
a partial page as the whole result set.

## Task-id strategy

There is one consistent rule: **a task id names one execution, and the events
with that id are the audit trail of exactly that execution.**

- **Batch pipeline** — the task id is the existing `run_id`. It is identical to
  the `agent_runs.run_id` document and the scheduler's run guard, so a batch
  timeline, its briefing and its saved results all share one id.
- **Chat** — each *turn* gets a fresh `uuid4` task id (the `thread_id` stays the
  conversation key). `stream_chat` sends that id to the client twice so the UI
  can watch its own execution: an `event: task` frame
  (`{"task_id","thread_id"}`) as the first frame of the turn, and `task_id` on
  the existing `done` frame. The turn's task id is also stored on the saved turn
  and the `chat_logs` document. These are additive: a client that ignores
  unknown event names still works, and the token/reset/error frames are
  unchanged.

- **ERP refresh** — each refresh cycle gets a fresh `uuid4` task id, used only
  for the `erp.sync_*` events. It is intentionally a different namespace from
  batch `run_id`s.

## Emission (`server/app/services/events.py`)

`EventService.emit(...)` is the only writer:

1. allocates `seq` with an atomic MongoDB `$inc`/upsert on `task_event_counters`
   — so concurrent items and concurrent workers never reuse a `(task_id, seq)`;
2. persists the event to `task_events` (`_id = event_id`; a unique
   `(task_id, seq)` index is created once, lazily, best-effort);
3. publishes the persisted event on the existing `dashboard:updates` channel as
   an `{"event": "task_event", "data": {...}}` envelope.

`emit()` **never raises**. If the sequence or persistence fails it logs (without
the payload) and returns `None`; if only the Redis publish fails the event is
already durable and the failure is logged. The graph wraps `emit` again so even
a misbehaving service cannot corrupt a run.

### Delivery guarantees (honest)

- MongoDB (`task_events`) is the **durable source of truth**.
- Redis pub/sub is **best-effort, at-most-once per connected client and not
  durable**. A client disconnected while an event is published misses it live
  and recovers it by replaying from Mongo. We do **not** claim at-least-once or
  exactly-once delivery over Redis.

## Endpoints (`server/app/api/`)

All under the existing `X-API-Key` auth (`app/api/security.py`) and served
through the Next.js `/backend` proxy.

- `GET /api/agents` — the registered capabilities (`app/agents/registry.py`)
  plus a status **derived from persisted `agent.*` events**. A never-run agent
  is `idle`, never `running`.
- `GET /api/tasks` — recent tasks collapsed from their newest events.
- `GET /api/tasks/{task_id}/events?after={seq}` — replay persisted events after
  `seq`, then keep streaming live ones over SSE. The stream: subscribes to Redis
  **before** reading the replay (so nothing published during replay is lost);
  delivers in `seq` order (out-of-order live events wait in a reorder buffer);
  deduplicates by `seq` and `event_id`; advances past a permanently missing head
  after a short timeout instead of stalling; uses its own shared Redis slot pool
  (`LIVE_MAX_CLIENTS`) and cleans up on disconnect. Unknown task → `404`;
  negative cursor → `422`.

The existing `GET /api/live` dashboard stream is unchanged: it **ignores**
`task_event` envelopes, so `snapshot`/`briefing`/`error` behave exactly as
before.

## Where events come from (Phase 2)

- **`server/app/agents/graph.py`** — `task.created/started/completed/failed`
  around the whole run (task id = `run_id`); `agent.queued/started/progress/
  completed/failed` for negotiation, collections, lead_followup and master.
  The three specialists now run as **parallel branches** (`load_signals` fans
  out to them; all three rejoin at `group_by_customer`) under a **single shared
  `asyncio.Semaphore(AGENT_CONCURRENCY)`**, so that value is the system-wide
  bound on concurrent LLM calls, not a per-branch limit. Cached items emit
  `agent.progress` with `cached=true` and make no LLM call. A failing
  specialist records its error and still returns, so one branch never discards
  the others. Money/contact validation, model-output repair, code-owned fields
  and the 24h cache are unchanged.
- **`server/app/agents/chat.py`** — `task.*` and `agent.*` per turn, and
  `tool.started` / `tool.completed` (with timing and success) around each real
  `invoke_tool` call. Only the registered tool name is recorded, never its
  arguments.
- **`server/app/services/scheduler.py`** — `erp.sync_started/completed/failed`
  around the actual incremental refresh; the trigger name is passed into the
  pipeline run so it lands in that run's `task.created`. Leader election,
  cadence, change detection and the daily full-run safety net are unchanged.

## Indexes (`server/app/services/indexes.py` + event service)

- `task_events` gets a newest-first `ts` read index from the shared index set,
  and the event service creates the **unique `(task_id, seq)`** index and a
  `(task_id, seq)`/`(task_id, ts)` pair lazily on first emit (best-effort).
- Ordering and `(task_id, seq)` uniqueness are guaranteed by the atomic counter
  even if the unique index cannot be created; the index is defence in depth.

## Client consumption

`client/lib/task-events.ts` parses the frames and reduces them into view state
(pure and unit-tested); `client/hooks/use-task-events.ts` owns the connection
(replay, cursor, capped backoff, abort on task change or unmount). The reducer
deduplicates by `event_id`, orders the timeline by `seq` (so a replayed or
out-of-order event cannot reorder what is already shown), and treats a terminal
task status as final - a late `task.started` cannot resurrect a finished run.
`/api/agents` status is only surfaced when an agent is genuinely `queued` or
`running`; a past `completed` from another task shows as idle, because the
registry reports last-known history rather than the task being viewed.
`client/lib/presentation.ts` is the only place that turns an event into
executive-facing wording - agent display names, activity phrases, the orb state
and the operation-flow stages. Display names are labels for the existing
registry ids (`master` -> "Master AI", `chat` -> "Chat Assistant"); no new agent
is implied. Internal ids, run keys, model names and raw payloads stay out of the
main view and behind the optional "Technical details" disclosure.

## Limitations

- `task.cancelled` is defined in the schema but no execution path emits it yet
  (there are no approvals/cancels and no cancel endpoint); the UI must not invent
  one, and dismissing the execution view is presentation only - backend work
  continues.
- Live delivery inherits Redis pub/sub's non-durability; the replay endpoint is
  the recovery path, not a guaranteed push.
