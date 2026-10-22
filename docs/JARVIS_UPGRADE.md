# JARVIS Deep Upgrade — capability map, phases, limitations

Implemented locally (uncommitted) on top of the intelligence layer
(commit 804fa71 + Command Center rounds). Everything below is backed by
the offline test suites: server 356 tests, client 156 tests.

## Capability map — what can be answered, from where

All business data comes from the read-only Mongo mirrors of the ERP.
There are no other sources, and no tool invents one.

| Domain | Source | Tools |
| --- | --- | --- |
| Payments | `payments_flat` | `get_payments`, `get_revenue`, `compare_periods`, `detect_anomalies` |
| Invoices / balances | `orders_flat` (invoice fields) | `get_invoices`, `get_top_unpaid`, `get_customer_profile` |
| Quotes | `quotes_flat` | `get_quotes`, `get_top_open_quotes` |
| Orders | `orders_flat` | `get_orders` |
| Leads | `leads` (14-day eligibility unchanged) | `get_leads`, `get_stuck_leads`, `compare_lead_pulls` |
| Customers | join across quotes/orders/payments | `find_customer`, `get_customer_context`, `get_customer_profile` |
| Reps | `users_flat` + the same mirrors | `get_rep_summary` |
| Briefings / drafts | `agent_runs` | `get_latest_briefing`, `get_agent_briefing` |
| Priorities | composed from signals + top lists + stuck leads | `get_priorities` |
| Schema / freshness of the mirrors themselves | the collections | `describe_data` |

Not supported (and the assistant must say so): order status timestamps
(the ERP stores none, so "stalled order" timing is unreliable), lead
outcomes inside customer context, contact details in lead tools, any
write-back to the ERP, and any company-document knowledge (see below).

## What changed by phase

- **P0 — correctness.** Conversation state now persists a bounded
  `pending_action` on the chat thread. A short confirmation ("yes pull",
  "show me all of them", "fetch the next page") executes the recorded
  read-only retrieval in the backend *before* the model runs; the model
  answers from the fresh results under a directive forbidding a repeated
  offer. `get_customer_profile` was rebuilt: sections (details, quotes,
  orders, payments, invoices) are fetched independently, a section
  failure marks only that section unavailable, nested section rows are
  bounded per section (previously an oversized profile collapsed into a
  single truncated preview string and lost every count), and the result
  carries `complete` + `missing_sections`. An answer that calls a
  partial profile complete is rejected by the consistency checks and
  regenerated. Identical tool calls inside one turn are no longer
  re-invoked: the earlier result is repeated, marked
  `duplicate_call_prevented`, and no tool activity is recorded for it.
- **P1 — orchestration.** The tool-call budget rose from 8 to 12 and the
  system guidance now requires combining sources in one block for
  360/comparison/why questions. Page walks merge rows deduplicated by
  record id; totals always come from the tool's own full-set metadata,
  never from counting listed rows. A walk that fetched every page but
  was trimmed by the context budget is reported as `walk_complete` —
  a display limit, not outstanding retrieval.
- **P2 — business intelligence.** `compare_periods` computes a metric
  for a period and the equal-length period before it (payments,
  invoices by outstanding balance, quotes, orders, leads by count),
  with absolute/percentage change; a zero previous total yields a null
  percentage labelled "new", never infinity. `detect_anomalies`
  compares a recent window (default 7 days) with a daily baseline
  (default 56 days) and flags only a >=30% deviation that is also
  >=2 standard deviations; with no data it says a trend cannot be
  established. It reports patterns, never causes. `get_priorities`
  composes ranked recommendations (overdue collections, stale quotes,
  stuck leads) from the existing Python signals, each with metrics,
  record ids, confidence, limitations and a human next step;
  a recommendation with no evidence behind it is not emitted.
- **P3 — visualization.** `tool.completed` payloads now carry the
  page `offset` a paged tool served. The execution tree derives a
  pagination stage from those events ("N pages fetched" on the leaf,
  and in the click-detail) only when more than one page was really
  fetched or a page starts past offset zero. Confirmed retrievals emit
  the same tool events as model-chosen calls, so the tree shows them.
- **P4 — validation.** New deterministic offline tests
  (`server/tests/test_chat_deep_upgrade.py`, 20 tests) cover the
  Garcoa "yes pull" reproduction end to end, pending-action lifecycle,
  profile completeness and failure isolation, duplicate prevention,
  comparison math (including zero denominators), anomaly fixtures,
  and grounded priorities. Full suites: server 356 passed, client 156
  passed, eslint clean, production build clean.

## Known limitations

- Confirmations are detected by a conservative matcher (short exact
  phrases and prefixes). A long sentence that begins with "yes" is
  treated as a new question, by design.
- Profile sections list at most one page each (50 rows); the pending
  action is what fetches the remainder, in walks of up to 10 pages per
  section (20 for a full page-walk). Listings remain bounded by the
  model context budget even when the walk is complete.
- `detect_anomalies` loads its source collection once per call and
  buckets days in Python; fine for the current mirror sizes.
- Data freshness is only as good as the mirrors: `describe_data` and
  `get_signals_summary.as_of` expose what exists; nothing here claims a
  source is fresh when its metadata cannot prove it.

## Future knowledge layer (not implemented)

No company-document retrieval or RAG exists in this codebase today.
A future authorized layer could index company SOPs, policies and
technical documents with source citations, kept strictly separate from
ERP analytics: document answers would cite document ids, ERP answers
cite tool evidence, and the two provenances would never be merged into
one claim. That requires its own decision on ingestion, storage and
permissions; this upgrade deliberately adds no embeddings, training,
new databases or ingestion jobs.

## Chat storage and cross-chat recall

Every conversation is stored on the server in `chat_threads` (turns,
title from the first question, the bounded last-query and pending-action
records). On top of that store:

- `GET /api/chats` lists all conversations (title, turn count, last
  activity); `GET /api/chats/{thread_id}` returns one in full;
  `DELETE /api/chats/{thread_id}` removes it (usage/audit logs stay).
- The **Chats tab** lists them in the app; opening one restores its
  saved turns in the Command Center and continues that same thread.
  The chat panel also restores the current conversation on load: the
  thread id is kept in `localStorage` (previously `sessionStorage`,
  which died with the tab session).
- **New chat** starts a fresh thread; the old one stays stored.
- The `recall_conversations` tool searches the stored chats, so JARVIS
  answers "what did we discuss about X" from real past exchanges. Its
  excerpts are deliberately kept out of the numeric evidence set: a
  figure from an old answer is context, and citing it as current still
  requires a fresh data tool call. This is retrieval over stored
  history — no model training or fine-tuning is involved, per the
  project's standing decision.
