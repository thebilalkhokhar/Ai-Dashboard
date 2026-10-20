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
