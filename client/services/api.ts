import {
  mockSignalsReport,
  voiceConsoleData,
} from "@/lib/mockData";
import type {
  ChatDoneEvent,
  ChatRequest,
  ChatStreamEvent,
  CustomerContext,
  HealthResponse,
  AgentRunStarted,
  LatestBriefingResponse,
  SignalsReport,
} from "@/types/api";

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

const CHAT_THREAD_STORAGE_KEY = "biztek-chat-thread-id";
let inMemoryChatThreadId: string | null = null;

function createThreadId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function getChatThreadId(): string {
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

export async function fetchSignals(): Promise<SignalsReport> {
  try {
    const response = await fetch("http://localhost:8000/api/signals", {
      cache: "no-store",
    });

    if (!response.ok) {
      throw new Error(`Signals request failed with status ${response.status}`);
    }

    return (await response.json()) as SignalsReport;
  } catch (error) {
    console.warn("Unable to load live signals; using mock signals report.", error);
    return mockSignalsReport;
  }
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch("http://localhost:8000/health", {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Health request failed with status ${response.status}`);
  }

  return (await response.json()) as HealthResponse;
}

export async function fetchCustomerContext(
  customerId: number,
  options: { leadLimit?: number } = {},
): Promise<CustomerContext> {
  if (!Number.isInteger(customerId) || customerId < 1) {
    throw new RangeError("Customer ID must be a positive integer.");
  }

  const query = new URLSearchParams();
  if (options.leadLimit !== undefined) {
    if (!Number.isInteger(options.leadLimit) || options.leadLimit < 1 || options.leadLimit > 500) {
      throw new RangeError("Lead limit must be an integer between 1 and 500.");
    }
    query.set("lead_limit", String(options.leadLimit));
  }

  const search = query.size > 0 ? `?${query.toString()}` : "";
  const response = await fetch(
    `http://localhost:8000/api/customers/${customerId}/context${search}`,
    { cache: "no-store" },
  );

  if (!response.ok) {
    throw new Error(`Customer context request failed with status ${response.status}`);
  }

  return (await response.json()) as CustomerContext;
}

export async function startAgentRun(): Promise<AgentRunStarted> {
  const response = await fetch("http://localhost:8000/api/agents/run", {
    method: "POST",
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Agent run request failed with status ${response.status}`);
  }

  return (await response.json()) as AgentRunStarted;
}

export async function fetchLatestBriefing(): Promise<LatestBriefingResponse> {
  const response = await fetch("http://localhost:8000/api/briefing/latest", {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Latest briefing request failed with status ${response.status}`);
  }

  return (await response.json()) as LatestBriefingResponse;
}

export async function sendMessage(
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<ChatDoneEvent> {
  const request: ChatRequest = {
    thread_id: getChatThreadId(),
    message,
  };
  const response = await fetch("http://localhost:8000/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`Chat request failed with status ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Chat response did not include a readable event stream.");
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

export async function submitApproval(
  threadId: string,
  approved: boolean,
): Promise<{ status: "success" }> {
  try {
    const response = await fetch("http://localhost:8000/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, approved }),
    });

    if (!response.ok) {
      throw new Error(`Approval request failed with status ${response.status}`);
    }

    return { status: "success" };
  } catch (error) {
    console.warn("Unable to submit approval; simulating successful response.", error);
    await wait(500);
    return { status: "success" };
  }
}

export async function sendVoiceAction(audioBlob: Blob): Promise<typeof voiceConsoleData> {
  try {
    const formData = new FormData();
    formData.append("audio", audioBlob, "voice-input.webm");

    const response = await fetch("http://localhost:8000/api/voice", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`Voice request failed with status ${response.status}`);
    }

    return (await response.json()) as typeof voiceConsoleData;
  } catch (error) {
    console.warn("Unable to submit voice input; using mock response.", error);
    await wait(2000);
    return voiceConsoleData;
  }
}
