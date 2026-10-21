# Biztek AI Command Center - backend

FastAPI reads ERP data from MongoDB and publishes it to the dashboard. Redis
holds the run lock, the shared rate limits, the snapshot and the signals cache.
Both services must be reachable. See the repository README for the full setup,
architecture diagram and environment variable table.

## Auth

Every `/api` route requires the `X-API-Key` header, matched against `API_KEYS`
in constant time; `/health` stays open for probes. `AUTH_ENABLED=false` is for
local development only and logs a loud warning at startup. CORS comes from
`CORS_ORIGINS`. Mongo indexes are created idempotently on startup
(`app/services/indexes.py`).

## Run

```sh
cd server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

`.env.example` lists every variable with a comment. The values that must never be
committed are `ERP_REFRESH_TOKEN`, `LLM_API_KEY` and `API_KEYS`.

## Implemented endpoints

- `GET /health`
- `GET /api/metrics`
- `GET /api/live` (SSE)
- `GET /api/signals?stale_days=&stalled_days=&overdue_days=&include_stalled=` (`include_stalled` defaults to `true`)
- `GET /api/leads/stuck?days=&limit=` (stuck leads, parsed by Python - no AI)
- `POST /api/agents/run` (one run at a time: a second concurrent call gets `409`
  with the `run_id` already in progress)
- `GET /api/briefing/latest` (now also carries `lead_followup` items and `counts.lead_followup_total`)
- `GET /api/customers/{id}/context?lead_limit=`
- `POST /api/chat` (SSE events: `tool`, `token`, `reset`, `done`, `error`)

## Tests and CI

`python -m pytest -q` runs the whole suite offline. GitHub Actions
(`.github/workflows/ci.yml`) runs it on every push and pull request, together
with the client's lint, unit tests and build.

## Agents

Three sub-agents plus the Master, all in `app/agents/`. Code detects and computes; the AI only interprets and drafts:

1. **Negotiation** - one stale quote per item.
2. **Collections** - one unpaid invoice per item.
3. **Lead follow-up** - one stuck lead per item: status `New` / `Attempted to Contact`, last activity within `LEAD_ACTIVE_DAYS`, a description present, not flagged by the lead desk. `processors.parse_lead_description` reads the three description shapes (JSON, `key: value`, prose) in pure Python and computes `missing_fields`. The artwork rule: **Missing only when the description is JSON with an explicitly empty `artwork` list**; a non-empty list is Provided; `key: value` and prose text are "Not stated" - never guessed.

All three reuse the same guardrails (invented contacts/amounts validators, greeting sanitising, money formatting, 24h input-hash cache, `needs_review` on failure). Every draft is stored text only: **nothing is ever sent to or written to the ERP or Zoho**, and the lead agent receives no email/phone/id fields (its `lead_id` is filled in by Python).

Run once from the CLI:

```sh
.venv/bin/python -m scripts.run_agents          # reuse the 24h cache
.venv/bin/python -m scripts.run_agents --fresh  # call the LLM for every item
```

## Not built yet

- `/api/voice`
- `/api/approve`
- Fulfillment agent
