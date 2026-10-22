// Executive-facing language for the Command Center.
//
// Every string here is a label for something the backend actually reported:
// agent ids come from GET /api/agents, event types and payloads from the task
// event stream, and tool names from real tool.started events. Nothing in this
// module invents progress, counts, percentages or actions.

import type { TaskEvent } from "@/types/api";
import type {
  AgentNodeState,
  AgentViewStatus,
  ExecutionPhase,
  TimelineEntry,
} from "@/lib/task-events";

// Friendly display names for the registered capabilities. These are presentation
// labels only - the ids are the backend's, and no new agent is implied.
export const AGENT_LABELS: Record<string, string> = {
  master: "Master AI",
  negotiation: "Negotiation Assistant",
  collections: "Collections Assistant",
  lead_followup: "Lead Follow-up Assistant",
  chat: "Chat Assistant",
};

export function agentLabel(agentId: string | null | undefined): string {
  if (!agentId) return "Assistant";
  return AGENT_LABELS[agentId] ?? "Assistant";
}

export function agentLabelFor(id: string): string {
  // An unknown id is still an internal identifier: never render it in the
  // executive view.
  return AGENT_LABELS[id] ?? "Assistant";
}

// The visual states the orb understands. Each maps to real system activity.
export type OrbState =
  | "idle"
  | "listening"
  | "thinking"
  | "executing"
  | "responding"
  | "completed"
  | "failed";

export const ORB_CAPTION: Record<OrbState, string> = {
  idle: "Standing by",
  listening: "Listening",
  thinking: "Understanding your request",
  executing: "Working on it",
  responding: "Preparing your report",
  completed: "Done",
  failed: "Couldn't complete that",
};

export interface OrbInput {
  execution: ExecutionPhase;
  // has the backend handed this turn a task id yet?
  hasTask: boolean;
  // did the backend report any tool or agent work for this task yet?
  hasAgentActivity: boolean;
  hasToolActivity: boolean;
}

export function deriveOrbState(input: OrbInput): OrbState {
  const { execution, hasTask, hasAgentActivity, hasToolActivity } = input;
  const evidence = hasAgentActivity || hasToolActivity;
  switch (execution) {
    case "failed":
    case "cancelled":
      return "failed";
    case "completed":
      return evidence ? "responding" : "completed";
    case "synthesizing":
      return "responding";
    case "running":
      return "executing";
    case "starting":
      // a task exists but has recorded no work yet: understanding, not doing
      if (evidence) return "executing";
      return hasTask ? "thinking" : "listening";
    default:
      return "idle";
  }
}

// Map a real agent view status to a short executive word.
export function agentStatusWord(status: AgentViewStatus): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Working";
    case "completed":
      return "Done";
    case "failed":
      return "Unavailable";
    default:
      return "Ready";
  }
}

// Registered chat tools -> honest, non-technical activity phrases. Only the
// tools the backend really exposes appear here; an unknown tool falls back to a
// broad phrase rather than a specific claim.
const TOOL_PHRASES: Record<string, string> = {
  get_dashboard_metrics: "Reviewing business metrics",
  get_signals_summary: "Reviewing business metrics",
  get_revenue: "Reviewing payment totals",
  get_payments: "Reviewing payment records",
  get_top_unpaid: "Reviewing unpaid invoices",
  get_invoices: "Reviewing invoice details",
  get_quotes: "Reviewing quote activity",
  get_top_open_quotes: "Reviewing open quotes",
  get_orders: "Reviewing order activity",
  get_leads: "Reviewing lead records",
  get_stuck_leads: "Reviewing leads awaiting details",
  get_customer_context: "Reviewing customer information",
  get_customer_profile: "Reviewing customer information",
  find_customer: "Looking up the customer",
  get_rep_summary: "Reviewing representative activity",
  get_latest_briefing: "Reviewing the latest report",
  get_agent_briefing: "Reviewing assistant recommendations",
  get_record: "Looking up a record",
  search_records: "Searching the business records",
  compare_lead_pulls: "Comparing the last two lead pulls",
  describe_data: "Checking what data is available",
};

export function toolPhrase(toolName: unknown): string {
  if (typeof toolName !== "string" || !toolName) return "Reviewing business data";
  return TOOL_PHRASES[toolName] ?? "Reviewing business data";
}

// Which registered specialist's territory a chat tool belongs to. The chat
// assistant runs every lookup itself; this mapping only says whose desk the
// work sits on, so the live view can draw the hand-off honestly: a branch
// appears only when a tool of that territory was really invoked on this
// task, and its state comes from that tool's own events. Tools mapped to
// "chat" are plain record lookups the assistant keeps for itself - they
// hang directly off the root instead of inventing a transfer.
const TOOL_DOMAIN_AGENTS: Record<string, string> = {
  get_top_unpaid: "collections",
  get_payments: "collections",
  get_invoices: "collections",
  get_revenue: "collections",
  get_top_open_quotes: "negotiation",
  get_quotes: "negotiation",
  get_stuck_leads: "lead_followup",
  get_leads: "lead_followup",
  compare_lead_pulls: "lead_followup",
  get_dashboard_metrics: "master",
  get_signals_summary: "master",
  get_latest_briefing: "master",
  get_agent_briefing: "master",
  get_rep_summary: "master",
  find_customer: "chat",
  get_customer_context: "chat",
  get_customer_profile: "chat",
  get_record: "chat",
  search_records: "chat",
  get_orders: "chat",
  describe_data: "chat",
};

export function toolDomainAgent(toolName: unknown): string {
  if (typeof toolName !== "string" || !toolName) return "chat";
  return TOOL_DOMAIN_AGENTS[toolName] ?? "chat";
}

const AGENT_START_PHRASES: Record<string, string> = {
  negotiation: "Reviewing stale quotes",
  collections: "Reviewing unpaid invoices",
  lead_followup: "Reviewing leads awaiting details",
  master: "Preparing the report",
  chat: "Understanding your request",
};

// A calm, human one-liner for an event. Falls back to a broad phrase, never to
// a fabricated step or a percentage.
export function describeEvent(event: TaskEvent): string {
  const payload = event.payload ?? {};
  switch (event.type) {
    case "task.created":
      return "Request received";
    case "task.started":
      return "Getting started";
    case "task.completed":
      return "Work finished";
    case "task.failed":
      return "Something went wrong";
    case "task.cancelled":
      return "Request stopped";
    case "agent.queued":
      return `${agentLabel(event.agent_id)} queued`;
    case "agent.started":
      return AGENT_START_PHRASES[event.agent_id ?? ""] ?? `${agentLabel(event.agent_id)} started`;
    case "agent.progress":
      if (payload.cached === true) return "Reused a recent result";
      return `${agentLabel(event.agent_id)} is working`;
    case "agent.completed":
      return `${agentLabel(event.agent_id)} finished`;
    case "agent.failed":
      return `${agentLabel(event.agent_id)} could not complete`;
    case "tool.started":
      return toolPhrase(payload.tool);
    case "tool.completed":
      return payload.ok === false ? "A lookup did not complete" : "Lookup finished";
    case "erp.sync_started":
      return "Refreshing business data";
    case "erp.sync_completed":
      return "Business data is up to date";
    case "erp.sync_failed":
      return "Business data refresh did not complete";
    case "data.filter_completed":
      return "Filtered the matched records";
    case "data.aggregation_completed":
      return "Totals calculated by the backend";
    case "chat.validation_started":
      return "Checking the answer against the records";
    case "chat.validation_completed":
      if (payload.outcome === "corrected") return "Answer corrected and verified";
      if (payload.outcome === "replaced") return "Answer rebuilt from verified figures";
      return "Answer verified against the records";
    case "chat.response_started":
      return "Writing the response";
    default:
      return "Assistant activity";
  }
}

// ------------------------------------------------------- live operation
//
// The operation panel and the overlay read the SAME pure description. Every
// phase is selected from a real event the backend recorded; when no specific
// event supports a narrower claim, a broader truthful phrase is used instead.

export type OperationTone = "idle" | "working" | "done" | "failed" | "cached";

export interface OperationTurn {
  prompt: string;
  taskId: string | null;
  message: string;
}

export interface OperationInput {
  turn: OperationTurn | null;
  execution: ExecutionPhase;
  timeline: TimelineEntry[];
  agents?: Record<string, AgentNodeState>;
}

export interface OperationView {
  phase: string;
  detail: string;
  agent: string | null;
  tone: OperationTone;
  // the state the phase was derived from, or null before evidence
  evidence: string | null;
}

// One honest caption per state-machine phase. The workspace, the panel and the
// overlay all read this, so one execution can never be described two ways.
export const PHASE_CAPTION: Record<ExecutionPhase, { phase: string; detail: string }> = {
  idle: {
    phase: "Ready",
    detail: "Ask JARVIS about payments, invoices, quotes, orders or leads.",
  },
  starting: {
    phase: "Receiving your request",
    detail: "Sending your request to the assistant.",
  },
  running: {
    phase: "Working on it",
    detail: "The assistant is working through your request.",
  },
  synthesizing: {
    phase: "Preparing your report",
    detail: "The answer is being written from the verified results.",
  },
  completed: {
    phase: "Response ready",
    detail: "The answer is in the conversation.",
  },
  failed: {
    phase: "Couldn't complete that",
    detail: "The request did not finish. You can try it again.",
  },
  cancelled: {
    phase: "Request stopped",
    detail: "The backend recorded this request as cancelled.",
  },
};

export function phaseTone(phase: ExecutionPhase): OperationTone {
  switch (phase) {
    case "completed":
      return "done";
    case "failed":
    case "cancelled":
      return "failed";
    case "idle":
      return "idle";
    default:
      return "working";
  }
}

function latestAgent(timeline: TimelineEntry[], agents: Record<string, AgentNodeState> = {}) {
  // Newest first: the first agent that reported work in this task.
  for (const entry of timeline) {
    if (
      entry.agent_id &&
      (entry.type === "agent.started" ||
        entry.type === "agent.queued" ||
        entry.type === "agent.progress" ||
        entry.type === "agent.completed" ||
        entry.type === "tool.started")
    ) {
      return agentLabel(entry.agent_id);
    }
  }
  const running = Object.values(agents).find(
    (node) => node.status === "running" || node.activeTools.length > 0,
  );
  return running ? agentLabel(running.id) : null;
}

function latestWorkingAgent(timeline: TimelineEntry[], agents: Record<string, AgentNodeState> = {}) {
  for (const entry of timeline) {
    if (
      entry.agent_id &&
      (entry.type === "agent.started" || entry.type === "tool.started" || entry.type === "agent.progress")
    ) {
      return agentLabel(entry.agent_id);
    }
  }
  const running = Object.values(agents).find(
    (node) => node.status === "running" || node.activeTools.length > 0,
  );
  return running ? agentLabel(running.id) : null;
}

export function deriveOperation(input: OperationInput): OperationView {
  const { turn, execution, timeline, agents } = input;
  if (!turn) {
    return {
      phase: PHASE_CAPTION.idle.phase,
      detail: PHASE_CAPTION.idle.detail,
      agent: null,
      tone: "idle",
      evidence: null,
    };
  }

  const agent = latestAgent(timeline, agents);
  const caption = PHASE_CAPTION[execution === "idle" ? "starting" : execution];
  const settled = execution === "completed" || execution === "failed"
    || execution === "cancelled" || execution === "synthesizing";

  if (execution === "failed") {
    return {
      phase: caption.phase,
      detail: turn.message || caption.detail,
      agent,
      tone: "failed",
      evidence: "execution.failed",
    };
  }
  if (settled) {
    return {
      phase: caption.phase,
      detail: caption.detail,
      // a completed turn keeps the assistant the events actually named
      agent: execution === "synthesizing" ? latestWorkingAgent(timeline, agents) ?? agent : agent,
      tone: phaseTone(execution),
      evidence: `execution.${execution}`,
    };
  }

  // starting / running: name the recorded step when the events support it,
  // otherwise stay on the honest generic caption.
  if (timeline.length > 0) {
    const latest = timeline[0];
    switch (latest.type) {
      case "task.created":
        return {
          phase: "Request received",
          detail: "Waiting for the assistant to record its first step.",
          agent,
          tone: "working",
          evidence: latest.type,
        };
      case "task.started":
        return {
          phase: "Understanding your request",
          detail: "The assistant has started on your request.",
          agent,
          tone: "working",
          evidence: latest.type,
        };
      case "agent.queued":
        return {
          phase: `Queued with ${agentLabel(latest.agent_id)}`,
          detail: "This step is waiting to begin.",
          agent,
          tone: "working",
          evidence: latest.type,
        };
      case "agent.started":
        return {
          phase: `Assigned to ${agentLabel(latest.agent_id)}`,
          detail: "The assigned assistant is working on your request.",
          agent: agentLabel(latest.agent_id),
          tone: "working",
          evidence: latest.type,
        };
      case "agent.progress":
        return {
          phase: latest.human,
          detail: "The assistant is still working through the request.",
          agent: latestWorkingAgent(timeline, agents) ?? agent,
          tone: latest.human.toLowerCase().includes("reused") ? "cached" : "working",
          evidence: latest.type,
        };
      case "agent.completed":
        return {
          phase: `${agentLabel(latest.agent_id)} finished`,
          detail: "Preparing the next step from the recorded results.",
          agent: agentLabel(latest.agent_id),
          tone: "done",
          evidence: latest.type,
        };
      case "agent.failed":
        return {
          phase: `${agentLabel(latest.agent_id)} could not complete`,
          detail: "The request is still tracked; the assistant will report the outcome.",
          agent: agentLabel(latest.agent_id),
          tone: "failed",
          evidence: latest.type,
        };
      case "tool.started":
        return {
          phase: toolPhrase(latest.toolName),
          detail: "Reading the relevant business records.",
          agent: latestWorkingAgent(timeline, agents) ?? agent,
          tone: "working",
          evidence: latest.type,
        };
      case "tool.completed":
        return {
          phase: latest.toolOk === false ? "A lookup did not complete" : "Lookup finished",
          detail: latest.human,
          agent: latestWorkingAgent(timeline, agents) ?? agent,
          tone: latest.toolOk === false ? "failed" : "working",
          evidence: latest.type,
        };
      case "data.filter_completed":
        return {
          phase: "Records filtered",
          detail: latest.human,
          agent: latestWorkingAgent(timeline, agents) ?? agent,
          tone: "working",
          evidence: latest.type,
        };
      case "data.aggregation_completed":
        return {
          phase: "Totals calculated",
          detail: latest.human,
          agent: latestWorkingAgent(timeline, agents) ?? agent,
          tone: "working",
          evidence: latest.type,
        };
      case "chat.validation_started":
        return {
          phase: "Verifying the answer",
          detail: "Checking every figure in the answer against the recorded results.",
          agent: latestWorkingAgent(timeline, agents) ?? agent,
          tone: "working",
          evidence: latest.type,
        };
      case "chat.validation_completed":
        return {
          phase: "Answer verified",
          detail: latest.human,
          agent: latestWorkingAgent(timeline, agents) ?? agent,
          tone: "done",
          evidence: latest.type,
        };
      case "chat.response_started":
        return {
          phase: "Writing the response",
          detail: "Preparing the final answer from the verified results.",
          agent: latestWorkingAgent(timeline, agents) ?? agent,
          tone: "working",
          evidence: latest.type,
        };
      case "erp.sync_started":
        return {
          phase: "Refreshing business data",
          detail: "Pulling the latest records from the ERP.",
          agent,
          tone: "working",
          evidence: latest.type,
        };
      case "erp.sync_completed":
        return {
          phase: "Business data is up to date",
          detail: latest.human,
          agent,
          tone: "done",
          evidence: latest.type,
        };
      case "erp.sync_failed":
        return {
          phase: "Business data refresh did not complete",
          detail: "Cached records are still available.",
          agent,
          tone: "failed",
          evidence: latest.type,
        };
      default:
        break;
    }
  }

  return {
    phase: caption.phase,
    detail: execution === "starting" && !turn.taskId
      ? "Sending your request to the assistant."
      : caption.detail,
    agent,
    tone: phaseTone(execution),
    evidence: null,
  };
}

// ------------------------------------------------------- operation flow
//
// The active view shows the route a request really took, one stage per thing the
// backend recorded: the assistant picking the request up, the registered agents
// that reported work, the tools that were actually invoked, and the response. A
// stage only lights up when an event of this task supports it, and it is only
// drawn as finished when its own completion event arrived. Nothing here is
// timed, faked or interpolated.

export type StageState = "pending" | "active" | "done" | "failed";

export interface OperationStage {
  id: string;
  label: string;
  detail: string;
  state: StageState;
  // the event type this stage's state was read from
  evidence: string | null;
  // how many real calls one stage stands for
  count?: number;
}

export interface StageInput {
  execution: ExecutionPhase;
  timeline: TimelineEntry[];
  agents: Record<string, AgentNodeState>;
}

const MAX_TOOL_STAGES = 4;

export function deriveStages(input: StageInput): OperationStage[] {
  const { execution, timeline } = input;
  // events in the order the backend recorded them
  const ordered = [...timeline].reverse();
  const stages: OperationStage[] = [];

  const sawTaskStart = ordered.some(
    (entry) => entry.type === "task.started" || entry.type === "task.created",
  );
  stages.push({
    id: "intake",
    label: "Chat Assistant",
    detail: execution === "starting" ? "Reading your request" : "Request received",
    state: execution === "idle"
      ? "pending"
      : execution === "failed"
        ? "failed"
        : sawTaskStart || execution !== "starting"
          ? "done"
          : "active",
    evidence: sawTaskStart ? "task.started" : null,
  });

  // one stage per agent that actually reported on this task
  const agentIds: string[] = [];
  for (const entry of ordered) {
    if (entry.agent_id && entry.agent_id !== "chat" && !agentIds.includes(entry.agent_id)) {
      agentIds.push(entry.agent_id);
    }
  }
  for (const agentId of agentIds) {
    const entries = ordered.filter((entry) => entry.agent_id === agentId);
    const completed = entries.some((entry) => entry.type === "agent.completed");
    const failed = entries.some((entry) => entry.type === "agent.failed");
    stages.push({
      id: `agent-${agentId}`,
      label: agentLabel(agentId),
      detail: failed ? "Could not complete its part" : completed ? "Reported its results" : "Working through its part",
      state: failed ? "failed" : completed ? "done" : "active",
      evidence: failed ? "agent.failed" : completed ? "agent.completed" : "agent.started",
    });
  }

  // one stage per tool the backend actually invoked (same tool counted once)
  const calls: { tool: string; state: StageState; evidence: string | null }[] = [];
  for (const entry of ordered) {
    if (entry.type === "tool.started") {
      const existing = calls.find((call) => call.tool === entry.toolName);
      const record = { tool: entry.toolName ?? "", state: "active" as StageState, evidence: "tool.started" };
      if (existing) existing.state = "active";
      else calls.push(record);
    }
    if (entry.type === "tool.completed") {
      const existing = calls.find((call) => call.tool === entry.toolName);
      if (existing) {
        existing.state = entry.toolOk === false ? "failed" : "done";
        existing.evidence = entry.toolOk === false ? "tool.failed" : "tool.completed";
      }
    }
  }
  const shown = calls.slice(-MAX_TOOL_STAGES);
  for (const call of shown) {
    stages.push({
      id: `tool-${call.tool}`,
      label: toolPhrase(call.tool),
      detail:
        call.state === "failed"
          ? "That lookup did not complete"
          : call.state === "done"
            ? "Records read"
            : "Reading business records",
      state: call.state,
      evidence: call.evidence,
    });
  }
  if (calls.length > shown.length) {
    stages.push({
      id: "tool-more",
      label: "Earlier lookups",
      detail: `${calls.length - shown.length} more recorded before these`,
      state: "done",
      evidence: "tool.completed",
      count: calls.length - shown.length,
    });
  }

  const master = agentIds.includes("master");
  stages.push({
    id: "synthesize",
    // the intake node already says "Chat Assistant"; the head of the route is
    // the answer itself, so the tail never repeats the same label twice
    label: master ? "Master AI" : "Response",
    detail:
      execution === "synthesizing" || execution === "completed"
        ? "Writing the answer from the verified results"
        : "Waiting for the results",
    state:
      execution === "completed"
        ? "done"
        : execution === "synthesizing"
          ? "active"
          : execution === "failed"
            ? "failed"
            : "pending",
    evidence:
      execution === "completed" || execution === "synthesizing"
        ? master
          ? "agent.started"
          : "response.stream"
        : null,
  });

  return stages;
}

// ------------------------------------------------- execution tree model
//
// The big live view does not lay stages out for itself: this pure model
// groups the flat stage list into the tree the request actually formed -
// the routing root, one branch per specialist territory a real lookup
// touched, the lookups as leaves, the data stages each lookup genuinely
// performed (filtering with its real counts, aggregation) hanging off
// their leaf, then the answer-validation stage and the response. Every
// state and count is read from this task's own events and their bounded
// payloads; a stage that no event supports does not appear at all.

export interface TreeToolCounts {
  total: number;
  returned: number;
  remaining: number | null;
  complete: boolean;
}

export interface TreeToolStage {
  stage: OperationStage;
  tool: string;
  durationMs: number | null;
  counts: TreeToolCounts | null;
  filter: { matched: number; returned: number; remaining: number | null; complete: boolean } | null;
  aggregation: { aggregate: string } | null;
  pagination: { pages: number; lastOffset: number } | null;
}

export interface TreeBranch {
  agentId: string;
  label: string;
  state: StageState;
  tools: TreeToolStage[];
}

export interface ExecutionTree {
  root: OperationStage;
  branches: TreeBranch[];
  directTools: TreeToolStage[];
  agentStages: OperationStage[];
  moreStage: OperationStage | null;
  validation: { state: StageState; outcome: string | null } | null;
  response: OperationStage;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function deriveExecutionTree(
  input: StageInput & { details?: Record<string, Record<string, unknown>> },
): ExecutionTree {
  const details = input.details ?? {};
  const stages = deriveStages(input);
  const root = stages[0];
  const response = stages[stages.length - 1];
  const middle = stages.slice(1, -1);
  const ordered = [...input.timeline].reverse(); // oldest first

  const toolStage = (stage: OperationStage): TreeToolStage => {
    const tool = stage.id.slice("tool-".length);
    const completed = [...ordered]
      .reverse()
      .find((entry) => entry.type === "tool.completed" && entry.toolName === tool);
    const payload = completed ? details[completed.event_id] : undefined;
    const total = numberOrNull(payload?.records_total);
    const returnedCount = numberOrNull(payload?.records_returned);
    const counts: TreeToolCounts | null =
      total !== null && returnedCount !== null
        ? {
            total,
            returned: returnedCount,
            remaining: numberOrNull(payload?.records_remaining),
            complete: payload?.complete === true,
          }
        : null;

    const filterEntry = ordered.find(
      (entry) => entry.type === "data.filter_completed" && entry.toolName === tool,
    );
    const filterPayload = filterEntry ? details[filterEntry.event_id] : undefined;
    const matched = numberOrNull(filterPayload?.matched);
    const filterReturned = numberOrNull(filterPayload?.returned);
    const filter =
      matched !== null && filterReturned !== null
        ? {
            matched,
            returned: filterReturned,
            remaining: numberOrNull(filterPayload?.remaining),
            complete: filterPayload?.complete === true,
          }
        : null;

    // Pagination is real only when the backend walked pages: every
    // tool.completed for this tool carries the offset it served, so more
    // than one page (or a page that starts past zero) is a shown stage.
    const pageOffsets = ordered
      .filter((entry) => entry.type === "tool.completed" && entry.toolName === tool)
      .map((entry) => numberOrNull(details[entry.event_id]?.offset))
      .filter((value): value is number => value !== null);
    const pagination =
      pageOffsets.length > 1 || (pageOffsets.length === 1 && pageOffsets[0] > 0)
        ? { pages: Math.max(pageOffsets.length, 1), lastOffset: pageOffsets[pageOffsets.length - 1] ?? 0 }
        : null;

    const aggregationEntry = ordered.find(
      (entry) => entry.type === "data.aggregation_completed" && entry.toolName === tool,
    );
    const aggregationPayload = aggregationEntry ? details[aggregationEntry.event_id] : undefined;
    const aggregation =
      typeof aggregationPayload?.aggregate === "string"
        ? { aggregate: aggregationPayload.aggregate }
        : null;

    return {
      stage,
      tool,
      durationMs: completed?.durationMs ?? null,
      counts,
      filter,
      aggregation,
      pagination,
    };
  };

  const branches: TreeBranch[] = [];
  const directTools: TreeToolStage[] = [];
  for (const stage of middle) {
    if (!stage.id.startsWith("tool-") || stage.id === "tool-more") continue;
    const tool = toolStage(stage);
    const domain = toolDomainAgent(tool.tool);
    if (domain === "chat") {
      directTools.push(tool);
      continue;
    }
    let branch = branches.find((item) => item.agentId === domain);
    if (!branch) {
      branch = { agentId: domain, label: agentLabel(domain), state: "pending", tools: [] };
      branches.push(branch);
    }
    branch.tools.push(tool);
  }
  for (const branch of branches) {
    branch.state = branch.tools.some((tool) => tool.stage.state === "active")
      ? "active"
      : branch.tools.some((tool) => tool.stage.state === "failed")
        ? "failed"
        : branch.tools.every((tool) => tool.stage.state === "done")
          ? "done"
          : "pending";
  }

  const validationStarted = ordered.some((entry) => entry.type === "chat.validation_started");
  const validationCompleted = [...ordered]
    .reverse()
    .find((entry) => entry.type === "chat.validation_completed");
  let validation: ExecutionTree["validation"] = null;
  if (validationCompleted) {
    const payload = details[validationCompleted.event_id];
    validation = {
      state: "done",
      outcome: typeof payload?.outcome === "string" ? payload.outcome : null,
    };
  } else if (validationStarted) {
    validation = {
      state:
        input.execution === "failed" || input.execution === "cancelled" ? "failed" : "active",
      outcome: null,
    };
  }

  return {
    root,
    branches,
    directTools,
    agentStages: middle.filter((stage) => stage.id.startsWith("agent-")),
    moreStage: middle.find((stage) => stage.id === "tool-more") ?? null,
    validation,
    response,
  };
}

// ---------------------------------------------------- answer provenance
//
// The footer of every answer claims it was "verified against backend records".
// This turns that claim into something a reader can inspect: the tool calls this
// task actually recorded, each with the duration and outcome the backend
// reported. It reads only from the event timeline, so an answer with no
// recorded lookup shows no evidence rather than an implied one.

export interface AnswerEvidence {
  event_id: string;
  tool: string;
  label: string;
  durationMs: number | null;
  ok: boolean;
  at: string;
  // Counts the backend computed for this call and stored in the event payload.
  // Absent when the tool returns no paged record set - never guessed.
  records?: { total: number; returned: number; remaining: number | null; complete: boolean };
}

function recordCounts(
  payload: Record<string, unknown> | undefined,
): AnswerEvidence["records"] {
  if (!payload) return undefined;
  const total = payload.records_total;
  const returned = payload.records_returned;
  if (typeof total !== "number" || typeof returned !== "number") return undefined;
  if (!Number.isFinite(total) || !Number.isFinite(returned)) return undefined;
  const remaining = payload.records_remaining;
  return {
    total,
    returned,
    remaining: typeof remaining === "number" && Number.isFinite(remaining) ? remaining : null,
    // only the backend's own flag proves completeness; an absent flag is not true
    complete: payload.complete === true,
  };
}

export function answerEvidence(
  timeline: TimelineEntry[],
  details: Record<string, Record<string, unknown>> = {},
): AnswerEvidence[] {
  const opened = new Map<string, TimelineEntry>();
  const evidence: AnswerEvidence[] = [];
  // oldest first, so the calls read in the order they ran
  for (const entry of [...timeline].reverse()) {
    if (entry.type === "tool.started" && entry.toolName) {
      opened.set(entry.toolName, entry);
    }
    if (entry.type === "tool.completed" && entry.toolName) {
      const start = opened.get(entry.toolName);
      evidence.push({
        event_id: entry.event_id,
        tool: entry.toolName,
        label: toolPhrase(entry.toolName),
        durationMs: entry.durationMs ?? null,
        ok: entry.toolOk !== false,
        at: (start ?? entry).ts,
        records: recordCounts(details[entry.event_id]),
      });
      opened.delete(entry.toolName);
    }
  }
  // a lookup that started but never completed is still real activity - say so
  for (const entry of opened.values()) {
    evidence.push({
      event_id: entry.event_id,
      tool: entry.toolName ?? "",
      label: toolPhrase(entry.toolName),
      durationMs: null,
      ok: true,
      at: entry.ts,
    });
  }
  return evidence;
}

// A readable title for a task. Grounded in the user's own command when we have
// it; neutral otherwise. Never derived from the task id.
export function taskTitle(prompt: string | null | undefined): string {
  const text = (prompt ?? "").trim();
  if (!text) return "Assistant request";
  const firstSentence = text.split(/(?<=[.!?])\s/)[0] ?? text;
  const clipped = firstSentence.replace(/\s+/g, " ");
  return clipped.length > 72 ? `${clipped.slice(0, 69).trimEnd()}...` : clipped;
}
