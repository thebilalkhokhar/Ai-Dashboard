// Pure, deterministic task-event handling for the Command Center.
//
// Everything the visualization shows is derived from real TaskEvents the
// backend persisted. This module does NOT invent progress, percentages or agent
// activity: an idle agent stays idle until an agent.* event says otherwise, and
// an agent node only becomes "running" from a real agent.started.
//
// The reducer is pure and exported so the client tests can drive it with
// deterministic fixtures.

import type {
  RegistryAgent,
  TaskEvent,
  TaskEventType,
} from "@/types/api";
import { describeEvent } from "@/lib/presentation";

// ------------------------------------------------------------------ SSE parse

export const TASK_EVENT_ENVELOPE = "task_event";

// The browser hits the same-origin proxy base; the X-API-Key is added there
// server-side, so no secret is ever placed in a URL or in client code.
export function taskEventsUrl(base: string, taskId: string, after: number): string {
  return `${base}/api/tasks/${encodeURIComponent(taskId)}/events?after=${after}`;
}

// Parse one SSE block ("event: task_event\ndata: {...}") into a TaskEvent.
// Returns null for comment/heartbeat lines and any non-task_event frame, so the
// caller can ignore them without error. Throws only on malformed task_event JSON.
export function parseTaskEventBlock(block: string): TaskEvent | null {
  let eventName = "";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // SSE comment / heartbeat
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (eventName !== TASK_EVENT_ENVELOPE || dataLines.length === 0) return null;
  const parsed = JSON.parse(dataLines.join("\n")) as unknown;
  if (!isTaskEvent(parsed)) throw new Error("task_event frame had an invalid payload");
  return parsed;
}

function isTaskEvent(value: unknown): value is TaskEvent {
  if (!value || typeof value !== "object") return false;
  const e = value as Record<string, unknown>;
  return (
    typeof e.event_id === "string" &&
    typeof e.task_id === "string" &&
    typeof e.seq === "number" &&
    typeof e.type === "string"
  );
}

// ----------------------------------------------------------------- view state

export type AgentViewStatus =
  | "idle"
  | "queued"
  | "running"
  | "completed"
  | "failed";

export type TaskViewStatus =
  | "idle"
  | "created"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  // the backend has no terminal event for this task and its newest one is too
  // old to be live: genuinely unknown, never silently treated as running
  | "unknown";

// Reached only from a persisted task.cancelled event. The backend has no cancel
// endpoint for a chat turn, so nothing here may present dismissal as cancelling.
const TERMINAL_TASK_STATUSES: TaskViewStatus[] = ["completed", "failed", "cancelled"];

export function isTerminalTaskStatus(status: TaskViewStatus): boolean {
  return TERMINAL_TASK_STATUSES.includes(status);
}

export interface AgentNodeState {
  id: string;
  name: string;
  kind: string;
  status: AgentViewStatus;
  // real per-branch outcome counts, only when the backend reported them
  processed?: number;
  cached?: number;
  failed?: number;
  needsReview?: number;
  durationMs?: number;
  activeTools: string[];
  lastEventAt: string | null;
}

export interface TimelineEntry {
  event_id: string;
  seq: number;
  ts: string;
  type: TaskEventType;
  agent_id: string | null;
  // technical one-liner (diagnostics view)
  summary: string;
  // executive one-liner derived from the same real event
  human: string;
  // the registered tool identifier, only for real tool.* events
  toolName?: string;
  toolOk?: boolean;
  durationMs?: number;
}

export type StreamStatus =
  | "idle"
  | "connecting"
  | "live"
  | "reconnecting"
  | "offline"
  | "error";

// ------------------------------------------------------------- state machine
//
// One explicit execution state for the whole Command Center, derived - never
// stored. It combines what the chat stream told us about the turn with what the
// persisted task events say the backend actually did, and it is monotonic: a
// replayed, duplicated or out-of-order event can move the view back to an
// earlier stage but can never make a finished task look alive.

export type ExecutionPhase =
  | "idle"
  | "starting"
  | "running"
  | "synthesizing"
  | "completed"
  | "failed"
  | "cancelled";

export const LIVE_PHASES: ExecutionPhase[] = ["starting", "running", "synthesizing"];

export function isLivePhase(phase: ExecutionPhase): boolean {
  return LIVE_PHASES.includes(phase);
}

export interface ExecutionInput {
  // the turn the controller holds, or null when nothing is on screen
  turn: { phase: string; taskId: string | null } | null;
  taskStatus: TaskViewStatus;
  hasAgentActivity: boolean;
  hasToolActivity: boolean;
}

export function deriveExecution(input: ExecutionInput): ExecutionPhase {
  const { turn, taskStatus, hasAgentActivity, hasToolActivity } = input;
  if (!turn) return "idle";

  // Real backend state wins over the local turn: a task that ended - or died
  // while the browser was disconnected - must never stay visually active.
  if (taskStatus === "cancelled") return "cancelled";
  if (taskStatus === "failed") return "failed";
  if (turn.phase === "cancelled") return "cancelled";
  if (turn.phase === "failed") return "failed";
  if (taskStatus === "completed" || turn.phase === "completed") return "completed";

  if (turn.phase === "synthesizing") return "synthesizing";
  const hasEvidence = hasAgentActivity || hasToolActivity || taskStatus === "running";
  return hasEvidence ? "running" : "starting";
}

export interface CommandCenterState {
  taskId: string | null;
  taskStatus: TaskViewStatus;
  lastSeq: number;
  seenEventIds: Set<string>;
  agents: Record<string, AgentNodeState>;
  timeline: TimelineEntry[];
  toolRunning: Record<string, string>; // event tool name -> agent id
  // the bounded, safe payload of each event, kept for the timeline "details"
  details: Record<string, Record<string, unknown>>;
  // the connection state of the event stream itself
  streamStatus: StreamStatus;
  streamError: string;
}

export const TIMELINE_LIMIT = 200;

export function initialState(taskId: string | null = null): CommandCenterState {
  return {
    taskId,
    taskStatus: "idle",
    lastSeq: 0,
    seenEventIds: new Set(),
    agents: {},
    timeline: [],
    toolRunning: {},
    details: {},
    streamStatus: "idle",
    streamError: "",
  };
}

// Seed the visible nodes from the real /api/agents registry (idle until events).
export function seedAgents(state: CommandCenterState, agents: RegistryAgent[]): CommandCenterState {
  const next: Record<string, AgentNodeState> = {};
  for (const agent of agents) {
    const existing = state.agents[agent.id];
    next[agent.id] = existing ?? blankNode(agent);
  }
  return { ...state, agents: next };
}

function blankNode(agent: RegistryAgent): AgentNodeState {
  return {
    id: agent.id,
    name: agent.name,
    kind: agent.kind,
    status: registryHintStatus(agent),
    activeTools: [],
    lastEventAt: null,
  };
}

/**
 * `/api/agents` reports what each agent did last across *every* task, so a past
 * "completed" must not light a node up as Done in a view that shows no such
 * work. Only a status that is genuinely in flight may carry into the picture;
 * everything else presents as idle until this task's own events say otherwise.
 */
function registryHintStatus(agent: RegistryAgent): AgentViewStatus {
  if (agent.status_source !== "events") return "idle";
  return agent.status === "running" || agent.status === "queued" ? agent.status : "idle";
}

/**
 * The nodes to draw: every registered agent, with the selected task's live
 * event state winning over the registry's last-known status. Agents with no
 * event for this task keep their registry status, so an idle agent is still
 * shown as idle rather than silently dropped.
 */
export function mergeAgentNodes(
  registry: RegistryAgent[],
  live: Record<string, AgentNodeState>,
): AgentNodeState[] {
  return registry.map((agent) => {
    const current = live[agent.id];
    if (current) return current;
    return {
      id: agent.id,
      name: agent.name,
      kind: agent.kind,
      status: registryHintStatus(agent),
      activeTools: [],
      lastEventAt: null,
    };
  });
}

function ensureNode(
  agents: Record<string, AgentNodeState>,
  agentId: string,
): AgentNodeState {
  return (
    agents[agentId] ?? {
      id: agentId,
      name: agentId,
      kind: "specialist",
      status: "idle",
      activeTools: [],
      lastEventAt: null,
    }
  );
}

// ---------------------------------------------------------------- event reduce

export type CommandCenterAction =
  | { type: "reset"; taskId: string | null }
  | { type: "seed-agents"; agents: RegistryAgent[] }
  | { type: "event"; event: TaskEvent }
  | { type: "stream"; status: StreamStatus; error?: string };

export function commandCenterReducer(
  state: CommandCenterState,
  action: CommandCenterAction,
): CommandCenterState {
  switch (action.type) {
    case "reset":
      return initialState(action.taskId);
    case "seed-agents":
      return seedAgents(state, action.agents);
    case "event":
      return reduceEvent(state, action.event);
    case "stream":
      return { ...state, streamStatus: action.status, streamError: action.error ?? "" };
    default:
      return state;
  }
}

function reduceEvent(state: CommandCenterState, event: TaskEvent): CommandCenterState {
  // Stale-task guard: events from a previous task never touch a newer selection.
  if (state.taskId !== null && event.task_id !== state.taskId) return state;

  // Dedupe by event id (the server also dedups by seq on replay).
  if (state.seenEventIds.has(event.event_id)) return state;

  const seenEventIds = new Set(state.seenEventIds);
  seenEventIds.add(event.event_id);

  const agents = { ...state.agents };
  let taskStatus = state.taskStatus;
  const toolRunning = { ...state.toolRunning };

  if (event.agent_id) {
    const node = { ...ensureNode(agents, event.agent_id) };
    node.lastEventAt = event.ts;
    switch (event.type) {
      case "agent.queued":
        if (!isSettled(node.status)) node.status = "queued";
        break;
      case "agent.started":
        if (!isSettled(node.status)) node.status = "running";
        break;
      case "agent.progress":
        // progress keeps the agent running; it never implies completion
        if (!isSettled(node.status)) node.status = "running";
        break;
      case "agent.completed":
        node.status = "completed";
        node.processed = asNumber(event.payload.processed);
        node.cached = asNumber(event.payload.cached);
        node.failed = asNumber(event.payload.failed);
        node.needsReview = asNumber(event.payload.needs_review);
        node.durationMs = asNumber(event.payload.duration_ms);
        break;
      case "agent.failed":
        node.status = "failed";
        break;
      case "tool.started":
        if (event.payload.tool) node.activeTools = [...new Set([...node.activeTools, String(event.payload.tool)])];
        toolRunning[String(event.payload.tool)] = event.agent_id;
        break;
      case "tool.completed":
        node.activeTools = node.activeTools.filter((tool) => tool !== event.payload.tool);
        if (event.payload.tool) delete toolRunning[String(event.payload.tool)];
        break;
      default:
        break;
    }
    agents[event.agent_id] = node;
  }

  // A terminal state is final. A late replayed task.created (a gap-filled event
  // arriving after task.completed) may not resurrect a finished task.
  if (!isTerminalTaskStatus(state.taskStatus)) {
    switch (event.type) {
      case "task.created":
        taskStatus = "created";
        break;
      case "task.started":
        taskStatus = "running";
        break;
      case "task.completed":
        taskStatus = "completed";
        break;
      case "task.failed":
        taskStatus = "failed";
        break;
      case "task.cancelled":
        taskStatus = "cancelled";
        break;
      default:
        break;
    }
  }

  const entry: TimelineEntry = {
    event_id: event.event_id,
    seq: event.seq,
    ts: event.ts,
    type: event.type,
    agent_id: event.agent_id,
    summary: summarizeEvent(event),
    human: describeEvent(event),
    toolName:
      typeof event.payload.tool === "string" && event.payload.tool
        ? event.payload.tool
        : undefined,
    toolOk: typeof event.payload.ok === "boolean" ? event.payload.ok : undefined,
    durationMs: asNumber(event.payload.duration_ms),
  };
  const timeline = insertBySeq(state.timeline, entry);

  // Keep the details map aligned with the (bounded) timeline.
  const details: Record<string, Record<string, unknown>> = {
    ...state.details,
    [event.event_id]: event.payload,
  };
  for (const id of Object.keys(details)) {
    if (!timeline.some((item) => item.event_id === id)) delete details[id];
  }

  return {
    ...state,
    taskId: event.task_id,
    taskStatus,
    lastSeq: Math.max(state.lastSeq, event.seq),
    seenEventIds,
    agents,
    timeline,
    toolRunning,
    details,
  };
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function isSettled(status: AgentViewStatus): boolean {
  return status === "completed" || status === "failed";
}

// Newest first by sequence number, not by arrival: a reconnect that replays an
// older event leaves the reading order of the timeline unchanged.
function insertBySeq(entries: TimelineEntry[], entry: TimelineEntry): TimelineEntry[] {
  const next = [...entries];
  let index = 0;
  while (index < next.length && next[index].seq >= entry.seq) index += 1;
  next.splice(index, 0, entry);
  return next.slice(0, TIMELINE_LIMIT);
}

// A short, honest one-line label built only from real payload fields. It never
// states a percentage or a claim the backend did not provide.
export function summarizeEvent(event: TaskEvent): string {
  const p = event.payload;
  switch (event.type) {
    case "task.created":
      return `Task created${p.trigger ? ` (${String(p.trigger)})` : ""}`;
    case "task.started":
      return "Task started";
    case "task.completed": {
      const tokens = typeof p.total_tokens === "number" ? ` · ${p.total_tokens} tokens` : "";
      return `Task completed${tokens}`;
    }
    case "task.failed":
      return `Task failed${p.error ? `: ${String(p.error)}` : ""}`;
    case "task.cancelled":
      return "Task cancelled";
    case "agent.queued":
      return `Queued${p.total !== undefined ? ` (${p.total} items)` : ""}`;
    case "agent.started":
      return `Started${p.fresh !== undefined ? ` · ${p.fresh} to process` : ""}`;
    case "agent.progress": {
      if (p.cached === true) return "Reused cached result";
      if (p.status) return `Item ${String(p.status)}`;
      return "Progress";
    }
    case "agent.completed": {
      const parts = [
        p.processed !== undefined ? `${p.processed} processed` : "",
        p.cached !== undefined ? `${p.cached} cached` : "",
        p.failed ? `${p.failed} failed` : "",
        p.needs_review ? `${p.needs_review} review` : "",
      ].filter(Boolean);
      return parts.length ? `Completed · ${parts.join(", ")}` : "Completed";
    }
    case "agent.failed":
      return `Failed${p.error ? `: ${String(p.error)}` : ""}`;
    case "tool.started":
      return `Tool ${String(p.tool ?? "")} started`;
    case "tool.completed":
      return `Tool ${String(p.tool ?? "")} ${p.ok === false ? "failed" : "done"}`;
    case "erp.sync_started":
      return "ERP sync started";
    case "erp.sync_completed": {
      const newCount = sumNew(p);
      return newCount !== null ? `ERP sync completed · ${newCount} new records` : "ERP sync completed";
    }
    case "erp.sync_failed":
      return `ERP sync failed${p.error_kind ? `: ${String(p.error_kind)}` : ""}`;
    case "data.filter_completed": {
      const matched = typeof p.matched === "number" ? p.matched : null;
      const returned = typeof p.returned === "number" ? p.returned : null;
      return matched !== null && returned !== null
        ? `Filtered · ${matched} matched, ${returned} returned`
        : "Filtered";
    }
    case "data.aggregation_completed":
      return `Aggregation completed${p.aggregate ? ` · ${String(p.aggregate)}` : ""}`;
    case "chat.validation_started":
      return "Validation started";
    case "chat.validation_completed":
      return `Validation ${p.outcome ? String(p.outcome) : "completed"}`;
    case "chat.response_started":
      return "Response generation started";
    default:
      return event.type;
  }
}

function sumNew(payload: Record<string, unknown>): number | null {
  const keys = ["new_quotes", "new_orders", "new_payments"];
  let total = 0;
  let any = false;
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "number") {
      total += value;
      any = true;
    }
  }
  return any ? total : null;
}
