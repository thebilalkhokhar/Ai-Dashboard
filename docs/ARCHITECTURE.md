# Architecture

Biztek AI Command Center — a read-only dashboard over the Print247 ERP, with
LangGraph business agents, a tool-calling read-only chat assistant, and a
live, event-backed command center.

## Layout

- `server/` — FastAPI + LangGraph + MongoDB + Redis.
  - `app/config.py` — all settings, secrets only from `server/.env`.
  - `app/db.py` — one shared async Mongo client.
  - `app/services/` — `processors` (all arithmetic), `signals`, `metrics`,
    `refresh` (incremental ERP pull), `scheduler` (in-process loop + leader
    election), `redis_client`, `limits` (shared rate limit / SSE slots),
    `run_guard` (one run at a time), `indexes`, and **`events`** (task-event
    service).
  - `app/agents/` — `graph.py` (batch pipeline), `negotiation`/`collections`/
    `lead_followup`/`master`, `llm.py` (callers + `FakeCaller`), `chat.py` +
    `chat_tools.py` (single tool-calling assistant), and **`registry.py`** (the
    real agents the UI may show).
  - `app/schemas/` — Pydantic models, including **`events.py`**.
  - `app/api/` — `routes.py` (data + agents + tasks), `live.py` (SSE),
    `security.py` (X-API-Key).
- `client/` — Next.js 16 / React 19. Talks only to the same-origin `/backend`
  proxy, which injects `BACKEND_API_KEY` server-side.

## Invariants (do not break)

- **The LLM never does arithmetic.** Money, dates, counts and joins are computed
  in `processors`/Python and validated again in `graph.py` (invented amounts,
  invented contacts, glued-word repair, code-owned IDs/amounts, the code
  fallback master brief, the 24h input-hash cache). The numeric guard in
  `chat.py` accepts only numbers a tool returned this turn (or in the stored
  conversation), so a page's `total_count` / `returned` / `remaining_count` are
  reportable facts while an invented remainder is still rejected.
- **ERP is read-only.** No sends, no approvals, no writes. `/api/approve` and
  `/api/voice` are intentionally absent.
- **Record pages are honest.** Every list tool in `chat_tools.py` takes
  `offset`, sorts on a stable key with an id tiebreaker, and returns the block
  `offset, limit, total_count, returned, remaining_count, has_more, next_offset,
  all_records_returned, page, pages_total` computed from the rows that *survived*
  the context budget - so paging with `offset = next_offset` reaches every match
  exactly once, and a partial page can never read as the whole set. The page
  position is included because the guard only lets the model write numbers the
  backend computed; "page 1 of 6" is arithmetic the tool does, not the model.
- **The numeric guard accepts a restatement.** A number the reader put in their
  own question ("the last 7 days", "10 at a time") may be echoed, and a
  line-leading list ordinal is formatting rather than a claim. A figure the user
  *asked about* is not evidence for it: anything next to a `$` still has to come
  back from a tool.
- **Named periods are resolved server-side.** `app/services/periods.py` is the
  one place that decides what "this week", "last month" or "last 7 days" means,
  on the ERP's US Central business calendar: inclusive ends, Monday-to-Sunday
  weeks, and a "last ..." period being the previous *complete* one. Tools take
  an optional `period` and the argument validator turns it into the `date_from`
  / `date_to` pair the function already expects, so every tool shares one answer
  and no tool signature changed. Explicit dates always win; a name plus a date
  is refused rather than silently prioritised. Adding a name is one registry
  entry - `PERIOD_NAMES` is derived from it, so the schema and the error message
  update themselves.
- **A partial page cannot testify to a finished walk.** `all_records_returned`
  is `not has_more` **and** `offset <= total`: a page starting past the end
  delivered nothing and covered nothing, so it does not close the set. The
  numeric guard additionally rejects a sentence claiming the listing was
  complete while no page reported the walk finished ("no more payments remain"
  needs no numbers to be false). A walk that did reach the end may still say so,
  because the pages are read together.
- **A follow-up record says how old it is.** `query_context` stamps
  `recorded_at`; after six hours the continuation prompt tells the model that
  continuing keeps the *recorded* range - correct for "the rest", wrong for a
  fresh "today" - so a stale window is disclosed instead of silently reused.
  Records written before the stamp existed report no age rather than an invented
  one.
- **Auth is `X-API-Key`** on every `/api` route (except `/health`); the browser
  never holds a key. Rate limits and SSE capacity live in Redis.

## Batch pipeline

`run_pipeline` compiles a LangGraph that now fans out: `load_signals` (code) →
**{negotiation, collections, lead_followup} in parallel** → `group_by_customer`
(code) → `master` (LLM) → `save` (code). The three specialists share one
`asyncio.Semaphore(AGENT_CONCURRENCY)`, so concurrent LLM calls are bounded
system-wide, not per branch; a branch that fails records its error without
discarding the others. The whole run writes one `agent_runs` document keyed by
`run_id`.

## Live + events

`GET /api/live` relays the dashboard `snapshot`/`briefing`/`error` over SSE from
the Redis `dashboard:updates` channel. The **task-event layer** (see
`EVENTS.md`) persists `TaskEvent`s to Mongo `task_events`, publishes them on the
same channel, and exposes `GET /api/agents`, `GET /api/tasks` and the
replay-then-live `GET /api/tasks/{task_id}/events`. The dashboard stream ignores
task-event envelopes so its behaviour is unchanged.

The **Command Center** is the dashboard's first tab (`client/components/
command-center/`): an iridescent animated core inside a neural web of the
registered assistants, plus the one conversation and a live operation panel. It
has two views of the same truth and never shows both at once - the large orb and
agent web while idle, and the **operation flow** (assistant -> the agents and
tools that really ran -> the response) while a turn is on screen. That flow is
the active view whenever the Command Center tab is mounted; only a chat opened
from another tab gets the full-screen overlay (`execution-overlay.tsx`), so the
active view is rendered exactly once. Both entry points go through one
controller (`execution-context.tsx`), so a turn cannot be submitted twice, and
the answer always renders in the conversation that asked.

`chat.py` also keeps a bounded **last-query record** per thread
(`chat_threads.last_query`: tool, scalar filters, offset/limit/total/returned/
next_offset). A follow-up like "yes", "show all" or "get the rest" is restated
to the model from that record so it continues the same query with
`offset = next_offset` instead of losing the date range or switching dataset.
Rows themselves are never stored.

The stream is consumed by `hooks/use-task-events.ts` (replay + live, cursor,
capped backoff, abort on task change/unmount) feeding a pure reducer in
`lib/task-events.ts`; `lib/presentation.ts` maps events to executive wording.
The UI's state machine (`deriveExecution`: idle -> starting -> running ->
synthesizing -> completed/failed/cancelled) is **derived**, never stored, and a
terminal backend event ends it even if the chat stream never completed. Every
node state and timeline row derives from a real persisted event, and status is
always shown as text as well as colour or motion
(`hooks/use-reduced-motion.ts` plus a `prefers-reduced-motion` CSS block).
 Everything the command
center shows is backed by a persisted event — never a fabricated one.

## Scheduling

`DashboardScheduler` runs one cycle every `REFRESH_MINUTES` on the leader
worker: incremental refresh → snapshot store/publish → signals cache → event-
triggered agent run (only when item hashes changed) plus a daily full-run safety
net. Redis locks keep cycles and runs from overlapping.
