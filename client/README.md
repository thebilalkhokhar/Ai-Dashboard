# Biztek AI Command Center - dashboard

Next.js 16 dashboard for the FastAPI backend. The backend needs reachable
MongoDB and Redis services. See the repository README for the architecture,
environment table and how the pieces fit together.

## Run

```sh
cd client
npm install
cp .env.example .env.local
npm run dev            # http://localhost:3000
```

The browser calls same-origin `/backend/...`. The route handler in
`app/backend/[...path]/route.ts` runs on the server, adds the `X-API-Key` header
from `BACKEND_API_KEY` and streams the answer back, so the API key never ships
in browser code. `BACKEND_API_URL` says where to forward (default
`http://localhost:8000`). `NEXT_PUBLIC_API_BASE_URL` bypasses the proxy and is
only useful when the backend runs with `AUTH_ENABLED=false`.

## Tests and build

```sh
npm run lint
npm test               # Vitest + React Testing Library (tests/)
npm run build
```

The live-stream hook and the dashboard panels are tested with fixture data and
with the empty/error states; charts are stubbed because jsdom has no canvas.

## Backend endpoints used

- `GET /health`
- `GET /api/metrics`
- `GET /api/live` (SSE)
- `GET /api/signals?stale_days=&stalled_days=&overdue_days=&include_stalled=` (`include_stalled` defaults to `true`)
- `GET /api/leads/stuck?days=&limit=` (stuck leads, parsed by backend Python - no fake fallbacks)
- `POST /api/agents/run`
- `GET /api/briefing/latest` (negotiation, collections and `lead_followup` items)
- `GET /api/customers/{id}/context?lead_limit=`
- `POST /api/chat` (SSE events: `tool`, `token`, `reset`, `done`, `error`)

## Dashboard tabs

- **Overview** - KPI cards, including "Stuck leads: N" which links to the Leads tab.
- **Receivables** / **Pipeline** - unpaid invoices, stale quotes, stalled orders.
- **Leads** - table of stuck leads from `GET /api/leads/stuck`: name, company, status,
  days since activity, product, missing-field badges and artwork
  (Provided / Missing / Not stated). "Missing" appears only when the backend found
  an explicitly empty artwork list - the UI never computes this itself.
- **AI review** - human review of negotiation, collections and lead follow-up drafts
  (filter chip "Lead follow-up"). Everything is read-only: drafts are stored text,
  nothing is ever sent to the ERP or Zoho.

## Not built yet

- `/api/voice`
- `/api/approve`
- Fulfillment agent
