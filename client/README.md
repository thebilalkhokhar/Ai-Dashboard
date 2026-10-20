# Biztek AI Command Center: Frontend Development Specification

This document defines the structural boundaries, component requirements, and API contracts for the Next.js frontend. The frontend operates strictly as the presentation and interaction layer; the FastAPI backend handles all business logic, arithmetic, database queries, and AI routing[cite: 2].

## 1. Development Workflow & Boundaries

To enable parallel development within our monorepo without blocking each other, adhere to the following workflow:

*   **Mock-First Development:** Build all UI components using hardcoded JSON that matches the defined Pydantic API schemas. Do not wait for live backend endpoints to be finished.
*   **Zero Business Logic:** The frontend must never calculate metrics, sum totals, or format raw database timestamps. The FastAPI backend will deliver 100% pre-calculated, deterministic JSON[cite: 2].
*   **State Management:** Do not over-cache metric data in React state. Rely on the backend's Server-Sent Events (SSE) stream to push fresh data directly to the UI components[cite: 2, 3].

## 2. Core UI Components

The Next.js frontend requires four primary interfaces[cite: 2, 3]:

*   **CEO Command Dashboard:** Built with Next.js, Tailwind CSS, and shadcn/ui. Must include ECharts tiles that listen to SSE streams for live metric updates[cite: 2, 3].
*   **Voice Console:** A push-to-talk audio interface supporting bilingual Urdu/English code-switching and streaming audio replies[cite: 2, 3].
*   **Approval Inbox:** A critical UI component for the Human-in-the-Loop workflow. When the AI proposes a sensitive write action, the backend pauses execution[cite: 2]. This inbox must display the pending action and provide "Approve" and "Reject" buttons[cite: 3].
*   **Text Chat Interface:** A standard chat UI for executing ad-hoc CEO queries, capable of rendering structured JSON responses and Markdown[cite: 3].

## 3. API Contract & Endpoints

The frontend will interact exclusively with the FastAPI backend through the following endpoints. Authentication is handled via JWT tokens, and CORS is managed by the Caddy reverse proxy at the edge layer[cite: 2, 3].

| Endpoint | Method | Data Format | Purpose |
| :--- | :--- | :--- | :--- |
| `/api/chat` | POST | JSON / SSE | Triggers ad-hoc CEO queries and returns the AI's response via Server-Sent Events (SSE) streaming[cite: 2, 3]. |
| `/api/live` | GET | SSE | Continuous stream connection pushing real-time metric updates to the dashboard ECharts tiles[cite: 2, 3]. |
| `/api/approve` | POST | JSON | Transmits a `{ thread_id, approved: true/false }` payload to resume a paused LangGraph workflow[cite: 2, 3]. |
| `/api/voice` | POST | Audio/Blob | Processes push-to-talk streaming audio for the STT and TTS pipelines[cite: 2, 3]. |

## 4. Integration Guidelines for Approval Inbox (LangGraph Interrupts)

The backend uses LangGraph's native `interrupt()` function to pause execution before performing critical database writes (e.g., reassigning orders)[cite: 2]. 

1.  When a user requests an action via `/api/chat`, the backend may return a status payload such as `{"status": "paused_for_approval", "thread_id": "12345", "action_details": "..."}`.
2.  The frontend must catch this specific status and render a pending card in the Approval Inbox component[cite: 3].
3.  When the user clicks "Approve", the frontend sends a POST request to `/api/approve` with the `thread_id` so the backend can resume the exact workflow state[cite: 2, 3].
## 5. Integration status (implemented frontend)

The frontend now calls the real FastAPI routes and has no mock-data fallback. The
API base URL defaults to `http://localhost:8000` and can be overridden with
`NEXT_PUBLIC_API_BASE_URL`.

| UI surface | Backend route | Notes |
| :--- | :--- | :--- |
| KPI cards, charts, top reps | `GET /api/metrics` + `GET /api/live` | One shared `EventSource` updates every tile from `snapshot` events. |
| Receivables / pipeline tables | `GET /api/signals?stale_days=&stalled_days=&overdue_days=` | Thresholds are the backend's own query parameters. Tables render 25 rows at a time; totals are never computed in the browser. |
| Customer context sheet | `GET /api/customers/{id}/context` | Lookup is by numeric customer ID only. |
| AI review queue | `GET /api/briefing/latest`, `POST /api/agents/run` | Starting a run polls until the new `run_id` is saved, then shows the briefing. |
| Text chat | `POST /api/chat` (SSE) | Real model deltas: streams `tool`, `token`, `reset` (discard provisional text), `done` and `error` events. |
| Health badge | `GET /health` | Shows MongoDB state, database name and whether the scheduler is enabled. |

### Backend pieces that are missing or incomplete

These are surfaced in the UI rather than simulated:

* **No approval endpoint.** `POST /api/approve` returns 404 and there is no LangGraph
  `interrupt()` in `server/app`. The review queue is therefore read-only: it drafts
  messages and lets a reviewer copy them; nothing is sent or written to the ERP.
* **No voice endpoint.** `POST /api/voice` returns 404. The voice tab explains the
  required STT/TTS contract instead of pretending to transcribe.
* **No list endpoints** for all orders, quotes, leads or users, and no customer
  search. The dashboard shows the signal-derived rows the backend exposes and looks
  up customers by ID.
* **No authentication or JWT.** CORS only allows `http://localhost:3000` and
  `http://127.0.0.1:3000`; a deployed origin needs backend/Caddy configuration.
* **No pagination on `/api/signals`.** The response includes every stalled order
  (~1.3 MB in the current data), even though the backend marks that signal unreliable;
  the client only renders a window of rows.
* **Snapshot freshness.** `/api/metrics` and the SSE `snapshot` event are produced by
  the scheduler (`REFRESH_MINUTES`), while `/api/signals` is recomputed per request,
  so the two can disagree slightly between cycles.
