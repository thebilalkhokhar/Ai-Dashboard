// Thin client for the FastAPI backend (server/app).
//
// Only endpoints that actually exist are called. There is deliberately no mock
// fallback: if the backend is down the UI shows the error instead of pretending
// to have data.

import type {
  AgentRunStarted,
  AgentsResponse,
  TasksResponse,
  ChatDoneEvent,
  ChatRequest,
  ChatStreamEvent,
  ChatToolResultEvent,
  CustomerContext,
  DashboardSnapshot,
  HealthResponse,
  LatestBriefingResponse,
  SignalsQuery,
  SignalsReport,
  StuckLeadsResponse,
  TaskEvent,
} from "@/types/api";
import { parseTaskEventBlock, taskEventsUrl } from "@/lib/task-events";

// Where the browser sends backend calls.
//
// By default this is the same-origin proxy (/backend/... -> Next.js route
// handler -> FastAPI) because that is the only place the X-API-Key exists:
// anything under NEXT_PUBLIC_* is compiled into the browser bundle, so a real
// key must never be put in one. Set NEXT_PUBLIC_API_BASE_URL to the FastAPI
// URL only when the backend runs with AUTH_ENABLED=false (local development),
// e.g. NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev.
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "/backend"
).replace(/\/+$/, "");

const inFlightSignals = new Map<string, Promise<SignalsReport>>();

export class ApiError extends Error {
  readonly status: number;
  /** the parsed JSON body, when the backend sent one (409 carries its run_id) */
  readonly body: unknown;

  constructor(message: string, status = 0, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function describeDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((entry) => {
        if (entry && typeof entry === "object" && "msg" in entry) {
          const path = Array.isArray((entry as { loc?: unknown }).loc)
            ? ((entry as { loc: unknown[] }).loc.join("."))
            : "";
          return `${path ? `${path}: ` : ""}${String((entry as { msg: unknown }).msg)}`;
        }
        return JSON.stringify(entry);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return "";
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      cache: "no-store",
      ...init,
    });
  } catch {
    throw new ApiError(
      `Cannot reach the backend at ${API_BASE_URL}. Start FastAPI and try again.`,
      0,
    );
  }

  if (!response.ok) {
    let detail = "";
    let payload: unknown;
    try {
      payload = await response.json();
      if (payload && typeof payload === "object" && "detail" in payload) {
        detail = describeDetail((payload as { detail: unknown }).detail);
      }
    } catch {
      // non-JSON error body: fall back to the status code
    }
    throw new ApiError(
      detail || `Backend request failed (HTTP ${response.status}).`,
      response.status,
      payload,
    );
  }

  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

export function fetchMetrics(): Promise<DashboardSnapshot> {
  return apiFetch<DashboardSnapshot>("/api/metrics");
}

export function fetchSignals(query: SignalsQuery = {}): Promise<SignalsReport> {
  const params = new URLSearchParams();
  if (query.staleDays !== undefined) params.set("stale_days", String(query.staleDays));
  if (query.stalledDays !== undefined) params.set("stalled_days", String(query.stalledDays));
  if (query.overdueDays !== undefined) params.set("overdue_days", String(query.overdueDays));
  if (query.includeStalled !== undefined) {
    params.set("include_stalled", String(query.includeStalled));
  }
  const search = params.size > 0 ? `?${params.toString()}` : "";
  const path = `/api/signals${search}`;
  const pending = inFlightSignals.get(path);
  if (pending) return pending;

  const request = apiFetch<SignalsReport>(path);
  inFlightSignals.set(path, request);
  const clearRequest = () => {
    if (inFlightSignals.get(path) === request) inFlightSignals.delete(path);
  };
  void request.then(clearRequest, clearRequest);
  return request;
}

export function fetchCustomerContext(
  customerId: number,
  options: { leadLimit?: number } = {},
): Promise<CustomerContext> {
  if (!Number.isInteger(customerId) || customerId < 1) {
    return Promise.reject(new RangeError("Customer ID must be a positive integer."));
  }

  const query = new URLSearchParams();
  if (options.leadLimit !== undefined) {
    if (
      !Number.isInteger(options.leadLimit) ||
      options.leadLimit < 1 ||
      options.leadLimit > 500
    ) {
      return Promise.reject(
        new RangeError("Lead limit must be an integer between 1 and 500."),
      );
    }
    query.set("lead_limit", String(options.leadLimit));
  }

  const search = query.size > 0 ? `?${query.toString()}` : "";
  return apiFetch<CustomerContext>(
    `/api/customers/${customerId}/context${search}`,
  );
}

// GET /api/leads/stuck - parsed by backend Python only, no AI involved.
// Deduped in flight so StrictMode's double effect mount makes one request.
const inFlightStuckLeads = new Map<string, Promise<StuckLeadsResponse>>();

export function fetchStuckLeads(
  query: { days?: number; limit?: number } = {},
): Promise<StuckLeadsResponse> {
  const params = new URLSearchParams();
  if (query.days !== undefined) params.set("days", String(query.days));
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  const search = params.size > 0 ? `?${params.toString()}` : "";
  const path = `/api/leads/stuck${search}`;
  const pending = inFlightStuckLeads.get(path);
  if (pending) return pending;

  const request = apiFetch<StuckLeadsResponse>(path);
  inFlightStuckLeads.set(path, request);
  const clearRequest = () => {
    if (inFlightStuckLeads.get(path) === request) inFlightStuckLeads.delete(path);
  };
  void request.then(clearRequest, clearRequest);
  return request;
}

export type AgentRunStart =
  | { status: "started"; run_id: string }
  | { status: "already_running"; run_id?: string; detail?: string };

// 409 is not a failure: a run is already in progress and its id comes back,
// so the caller can simply wait for that one.
export async function startAgentRun(): Promise<AgentRunStart> {
  try {
    const started = await apiFetch<AgentRunStarted>("/api/agents/run", { method: "POST" });
    return { status: "started", run_id: started.run_id };
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      const body = error.body as { run_id?: unknown; detail?: unknown } | undefined;
      return {
        status: "already_running",
        run_id: typeof body?.run_id === "string" ? body.run_id : undefined,
        detail: typeof body?.detail === "string" ? body.detail : error.message,
      };
    }
    throw error;
  }
}

export function fetchLatestBriefing(): Promise<LatestBriefingResponse> {
  return apiFetch<LatestBriefingResponse>("/api/briefing/latest");
}

// ---------------------------------------------------------- command center

export function fetchAgents(): Promise<AgentsResponse> {
  return apiFetch<AgentsResponse>("/api/agents");
}

export function fetchTasks(limit = 20): Promise<TasksResponse> {
  return apiFetch<TasksResponse>(`/api/tasks?limit=${limit}`);
}

// GET /api/tasks/{id}/events?after={seq} - replay then live, as SSE.
//
// Uses fetch + AbortController through the same-origin proxy (which injects the
// X-API-Key server-side), not EventSource: that gives controlled reconnection
// with a cursor, a clean abort when the task changes or the view unmounts, and
// a real HTTP status to distinguish an auth failure (401) or unknown task (404)
// from a dropped stream. Resolves when the stream ends; throws ApiError with the
// status on an error response so the caller can show the right state.
export async function openTaskEventStream(
  taskId: string,
  after: number,
  options: { signal: AbortSignal; onEvent: (event: TaskEvent) => void },
): Promise<void> {
  const { signal, onEvent } = options;
  let response: Response;
  try {
    response = await fetch(taskEventsUrl(API_BASE_URL, taskId, after), {
      cache: "no-store",
      headers: { Accept: "text/event-stream" },
      signal,
    });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new ApiError(
      `Cannot reach the backend at ${API_BASE_URL}. Start FastAPI and try again.`,
      0,
    );
  }

  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = describeDetail((body as { detail: unknown }).detail);
      }
    } catch {
      // non-JSON error body: fall back to the status
    }
    throw new ApiError(
      detail || `Task-event stream failed (HTTP ${response.status}).`,
      response.status,
    );
  }
  if (!response.body) {
    throw new ApiError("Task-event stream did not include a readable body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function dispatch(block: string) {
    let event: TaskEvent | null;
    try {
      event = parseTaskEventBlock(block);
    } catch {
      return; // a malformed frame is skipped, never fatal to the stream
    }
    if (event) onEvent(event);
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) dispatch(block);
      if (done) break;
    }
    if (buffer.trim()) dispatch(buffer);
  } finally {
    reader.releaseLock();
  }
}

function isAbort(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}


// ---------------------------------------------------------------- chat (SSE)

const CHAT_THREAD_STORAGE_KEY = "biztek-chat-thread-id";
let inMemoryChatThreadId: string | null = null;

function createThreadId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function getChatThreadId(): string {
  if (inMemoryChatThreadId) return inMemoryChatThreadId;

  try {
    // localStorage, not sessionStorage: the current conversation is stored
    // on the server under this id, and the id must survive a browser
    // restart so the conversation can be reopened exactly where it ended.
    const storedThreadId =
      window.localStorage.getItem(CHAT_THREAD_STORAGE_KEY) ??
      window.sessionStorage.getItem(CHAT_THREAD_STORAGE_KEY);
    if (storedThreadId) {
      inMemoryChatThreadId = storedThreadId;
      window.localStorage.setItem(CHAT_THREAD_STORAGE_KEY, storedThreadId);
      return storedThreadId;
    }

    inMemoryChatThreadId = createThreadId();
    window.localStorage.setItem(CHAT_THREAD_STORAGE_KEY, inMemoryChatThreadId);
  } catch {
    inMemoryChatThreadId = createThreadId();
  }

  return inMemoryChatThreadId;
}

export function setChatThreadId(threadId: string): void {
  inMemoryChatThreadId = threadId;
  try {
    window.localStorage.setItem(CHAT_THREAD_STORAGE_KEY, threadId);
  } catch {
    // storage unavailable: the in-memory id still identifies this session
  }
}

export function startNewChatThread(): string {
  const threadId = createThreadId();
  setChatThreadId(threadId);
  return threadId;
}

// ------------------------------------------------------- stored chats

export interface ChatThreadSummary {
  thread_id: string;
  title: string;
  turn_count: number;
  updated_at: string | null;
}

export interface ChatThreadTurn {
  question: string;
  answer: string;
  created_at?: string | null;
}

export interface ChatThreadDetail {
  thread_id: string;
  title: string;
  turns: ChatThreadTurn[];
  updated_at: string | null;
}

export async function fetchChatThreads(): Promise<ChatThreadSummary[]> {
  const data = await apiFetch<{ chats: ChatThreadSummary[] }>("/api/chats");
  return data.chats ?? [];
}

export function fetchChatThread(threadId: string): Promise<ChatThreadDetail> {
  return apiFetch<ChatThreadDetail>(`/api/chats/${encodeURIComponent(threadId)}`);
}

export async function deleteChatThread(threadId: string): Promise<void> {
  await apiFetch<{ deleted: boolean }>(`/api/chats/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
}

function parseChatEvent(block: string): ChatStreamEvent | null {
  let eventName = "";
  const dataLines: string[] = [];

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (!eventName || dataLines.length === 0) return null;

  let data: unknown;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    throw new Error(`Chat server sent malformed ${eventName} event data.`);
  }

  if (!data || typeof data !== "object") {
    throw new Error(`Chat server sent invalid ${eventName} event data.`);
  }

  switch (eventName) {
    case "task": {
      // First frame of the turn: the id this turn's real events are stored
      // under. Older backends never send it, so an unknown name is ignored.
      const frame = data as { task_id?: unknown; thread_id?: unknown };
      if (typeof frame.task_id === "string") {
        return {
          event: "task",
          data: {
            task_id: frame.task_id,
            thread_id: typeof frame.thread_id === "string" ? frame.thread_id : "",
          },
        };
      }
      break;
    }
    case "tool":
      if ("name" in data && typeof data.name === "string") {
        return { event: "tool", data: { name: data.name } };
      }
      break;
    case "tool_result": {
      // Verified facts about one completed lookup. Only known-typed fields are
      // kept so a changed backend shape cannot put junk into the UI.
      const frame = data as Record<string, unknown>;
      if (typeof frame.tool !== "string") break;
      const num = (value: unknown) =>
        typeof value === "number" && Number.isFinite(value) ? value : undefined;
      const window = frame.window as Record<string, unknown> | undefined;
      const result: ChatToolResultEvent = { tool: frame.tool };
      const total = num(frame.records_total);
      const returned = num(frame.records_returned);
      const remaining = num(frame.records_remaining);
      if (total !== undefined) result.records_total = total;
      if (returned !== undefined) result.records_returned = returned;
      if (remaining !== undefined) result.records_remaining = remaining;
      const recordCount = num(frame.record_count);
      if (recordCount !== undefined) result.record_count = recordCount;
      if (typeof frame.complete === "boolean") result.complete = frame.complete;
      if (typeof frame.money_label === "string") result.money_label = frame.money_label;
      const money = num(frame.money_total);
      if (money !== undefined) result.money_total = money;
      if (typeof frame.money_covers === "string") {
        result.money_covers = frame.money_covers;
      }
      if (window) {
        result.window = {
          date_from: typeof window.date_from === "string" ? window.date_from : null,
          date_to: typeof window.date_to === "string" ? window.date_to : null,
        };
      }
      return { event: "tool_result", data: result };
    }
    case "token":
      if ("text" in data && typeof data.text === "string") {
        return { event: "token", data: { text: data.text } };
      }
      break;
    case "reset":
      return { event: "reset", data: {} };
    case "done": {
      const frame = data as { text?: unknown; thread_id?: unknown; task_id?: unknown };
      if (typeof frame.text === "string" && typeof frame.thread_id === "string") {
        return {
          event: "done",
          data: {
            text: frame.text,
            thread_id: frame.thread_id,
            ...(typeof frame.task_id === "string" ? { task_id: frame.task_id } : {}),
          },
        };
      }
      break;
    }
    case "error":
      if ("message" in data && typeof data.message === "string") {
        return { event: "error", data: { message: data.message } };
      }
      break;
    default:
      return null;
  }

  throw new Error(`Chat server sent invalid ${eventName} event data.`);
}

export async function sendMessage(
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<ChatDoneEvent> {
  const request: ChatRequest = {
    thread_id: getChatThreadId(),
    message,
  };

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  } catch {
    throw new ApiError(
      `Cannot reach the backend at ${API_BASE_URL}. Start FastAPI and try again.`,
      0,
    );
  }

  if (!response.ok) {
    throw new ApiError(
      `Chat request failed (HTTP ${response.status}).`,
      response.status,
    );
  }
  if (!response.body) {
    throw new ApiError("Chat response did not include a readable event stream.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed: ChatDoneEvent | null = null;

  function dispatch(block: string) {
    const event = parseChatEvent(block);
    if (!event) return;

    onEvent(event);
    if (event.event === "error") {
      throw new Error(event.data.message);
    }
    if (event.event === "done") {
      completed = event.data;
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });

      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        dispatch(block);
      }

      if (done) break;
    }

    if (buffer.trim()) dispatch(buffer);
  } finally {
    reader.releaseLock();
  }

  if (!completed) {
    throw new Error("Chat stream ended before the assistant completed its response.");
  }
  return completed;
}
