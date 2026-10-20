# Biztek AI backend

FastAPI reads and writes dashboard data in MongoDB. Redis stores the current metrics snapshot and live updates. Both services must be reachable.

## Run

```sh
cd server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Set the needed environment variable names in `server/.env`: `MONGO_URI`, `MONGO_DB`, `REDIS_URL`, `ERP_REFRESH_TOKEN`, `LLM_PROVIDER`, `LLM_API_KEY`, `MODEL_SUB`, `MODEL_MASTER`.

## Implemented endpoints

- `GET /health`
- `GET /api/metrics`
- `GET /api/live` (SSE)
- `GET /api/signals?stale_days=&stalled_days=&overdue_days=&include_stalled=` (`include_stalled` defaults to `true`)
- `POST /api/agents/run`
- `GET /api/briefing/latest`
- `GET /api/customers/{id}/context?lead_limit=`
- `POST /api/chat` (SSE events: `tool`, `token`, `reset`, `done`, `error`)

## Not built yet

- `/api/voice`
- `/api/approve`
- Fulfillment agent
