# Biztek AI Command Center: Backend Architecture & Workflow Guide

This document outlines the internal backend mechanisms (FastAPI + LangGraph) to help the frontend team understand how data is processed, where it comes from, and how the AI interacts with the Next.js UI[cite: 2, 3].

## 1. Data Flow Boundaries (Live APIs vs. Local DB)

The backend strictly separates data fetching from AI reasoning to ensure 100% mathematical accuracy and prevent LLM hallucinations[cite: 1, 3]. 

*   **Live ERP Data:** Fresh operational data (e.g., active quotes, pending orders, recent payments) is fetched directly from the live ERP system APIs in real-time[cite: 3].
*   **Historical/Reference Data:** Older records and persistent system states are queried from our local MongoDB secondary read-replica[cite: 2, 3].
*   **Zero AI Math:** The AI models **never** perform arithmetic or count rows. Standard Python scripts handle all filtering, calculations, and data structuring before feeding a compiled JSON summary to the AI[cite: 3]. 

## 2. LangGraph AI Orchestration

The AI layer operates on a hierarchical state machine (LangGraph) running 24/7. It consists of specialized sub-agents that perform *cognitive analysis* on the pre-calculated data, supervised by a Master CEO Agent[cite: 3].

*   **Financial/Sales Sub-Agent:** Reads the Python-filtered `quotes` and `payments` JSON. It identifies conversion context, stalled negotiations, and pending balances (e.g., recognizing that a specific user group has a high quote volume but stalled payment velocity)[cite: 3].
*   **Operations Sub-Agent:** Reads the `orders` and `users` JSON to spot fulfillment bottlenecks, delayed shipments, or issues with high-value customer orders[cite: 3].
*   **Master CEO Agent:** Synthesizes the text insights from the sub-agents into a cohesive 3-to-4 bullet executive brief, formatting it for the Next.js dashboard[cite: 3].

## 3. Human-in-the-Loop (Approval Workflow)

The backend acts as a strict gatekeeper for any database write actions (e.g., reassigning a lead, dispatching an order)[cite: 1, 2]. 

1.  When the AI proposes a write action, the LangGraph engine calls a native `interrupt()` function[cite: 2].
2.  The backend freezes the entire workflow state in memory and sends a `{"status": "paused_for_approval", "thread_id": "..."}` payload to the frontend[cite: 2].
3.  The frontend displays this in the "Approval Inbox". 
4.  Once the user clicks "Approve", the frontend hits `/api/approve` with the `thread_id`, and the backend resumes the exact Python thread to execute the ERP write action safely[cite: 2].

## 4. Primary API Handshakes

The Next.js frontend will consume these FastAPI endpoints to maintain synchronization with the backend graph[cite: 2].

| Endpoint | Protocol | Payload / Format | Backend Action |
| :--- | :--- | :--- | :--- |
| `/api/chat` | REST (POST) | `{"thread_id": "...", "message": "..."}` | Triggers the Master CEO Agent workflow. Returns standard JSON for quick answers or an interrupt status for write actions[cite: 2]. |
| `/api/live` | SSE (GET) | Server-Sent Events | The backend runs background Cron jobs to fetch live ERP API data, process it via Python, and push instant real-time updates through Redis Pub/Sub to the frontend ECharts tiles[cite: 2, 3]. |
| `/api/approve` | REST (POST) | `{"thread_id": "...", "approved": true}` | Unfreezes a paused LangGraph thread to complete an action[cite: 2]. |
| `/api/voice` | REST (POST) | Audio Blob | Routes push-to-talk audio through the backend STT (Urdu/English) pipeline, processes the query, and returns a TTS audio buffer[cite: 2]. |

**Frontend Takeaway:** Treat the backend as a deterministic JSON engine. The frontend should purely focus on state management, reacting to SSE pushes, and rendering the Approval Inbox when the backend signals a paused thread[cite: 2, 3].