# Biztek AI dashboard

Next.js dashboard for the FastAPI backend. The backend needs reachable MongoDB and Redis services.

## Run

```sh
cd client
npm install
npm run dev
```

The frontend uses `http://localhost:8000` by default. Set `NEXT_PUBLIC_API_BASE_URL` to another backend origin when needed.

## Backend endpoints used

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
