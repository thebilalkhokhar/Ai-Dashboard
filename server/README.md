# Biztek AI backend

FastAPI reads and writes dashboard data in MongoDB. Redis stores the current metrics snapshot and live updates. Both services must be reachable.

## Run

```sh
cd server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Set the needed environment variable names in `server/.env`: `MONGO_URI`, `MONGO_DB`, `REDIS_URL`, `ERP_REFRESH_TOKEN`, `LLM_PROVIDER`, `LLM_API_KEY`, `MODEL_SUB`, `MODEL_MASTER`, `LEAD_ACTIVE_DAYS` (stuck-lead activity window in days, default 14). See `.env.example` for the full name list - names only, never commit values.

## Implemented endpoints

- `GET /health`
- `GET /api/metrics`
- `GET /api/live` (SSE)
- `GET /api/signals?stale_days=&stalled_days=&overdue_days=&include_stalled=` (`include_stalled` defaults to `true`)
- `GET /api/leads/stuck?days=&limit=` (stuck leads, parsed by Python - no AI)
- `POST /api/agents/run`
- `GET /api/briefing/latest` (now also carries `lead_followup` items and `counts.lead_followup_total`)
- `GET /api/customers/{id}/context?lead_limit=`
- `POST /api/chat` (SSE events: `tool`, `token`, `reset`, `done`, `error`)

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
