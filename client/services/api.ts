// Thin client for the FastAPI backend (server/app).
//
// Only endpoints that actually exist are called. There is deliberately no mock
// fallback: if the backend is down the UI shows the error instead of pretending
// to have data.

import type {
  AgentRunStarted,
  ChatDoneEvent,
  ChatRequest,
  ChatStreamEvent,
  CustomerContext,
  DashboardSnapshot,
  HealthResponse,
  LatestBriefingResponse,
  SignalsQuery,
  SignalsReport,
} from "@/types/api";

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

const inFlightSignals = new Map<string, Promise<SignalsReport>>();

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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
    try {
      const payload: unknown = await response.json();
      if (payload && typeof payload === "object" && "detail" in payload) {
        detail = describeDetail((payload as { detail: unknown }).detail);
      }
    } catch {
      // non-JSON error body: fall back to the status code
    }
    throw new ApiError(
      detail || `Backend request failed (HTTP ${response.status}).`,
      response.status,
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

export function startAgentRun(): Promise<AgentRunStarted> {
  return apiFetch<AgentRunStarted>("/api/agents/run", { method: "POST" });
}

export function fetchLatestBriefing(): Promise<LatestBriefingResponse> {
  return apiFetch<LatestBriefingResponse>("/api/briefing/latest");
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
    const storedThreadId = window.sessionStorage.getItem(CHAT_THREAD_STORAGE_KEY);
    if (storedThreadId) {
      inMemoryChatThreadId = storedThreadId;
      return storedThreadId;
    }

    inMemoryChatThreadId = createThreadId();
    window.sessionStorage.setItem(CHAT_THREAD_STORAGE_KEY, inMemoryChatThreadId);
  } catch {
    inMemoryChatThreadId = createThreadId();
  }

  return inMemoryChatThreadId;
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
    case "tool":
      if ("name" in data && typeof data.name === "string") {
        return { event: "tool", data: { name: data.name } };
      }
      break;
    case "token":
      if ("text" in data && typeof data.text === "string") {
        return { event: "token", data: { text: data.text } };
      }
      break;
    case "reset":
      return { event: "reset", data: {} };
    case "done":
      if (
        "text" in data &&
        typeof data.text === "string" &&
        "thread_id" in data &&
        typeof data.thread_id === "string"
      ) {
        return {
          event: "done",
          data: { text: data.text, thread_id: data.thread_id },
        };
      }
      break;
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
