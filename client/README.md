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