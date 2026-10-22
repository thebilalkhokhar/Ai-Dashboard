import { describe, expect, it } from "vitest";

import {
  commandCenterReducer,
  deriveExecution,
  initialState,
  isLivePhase,
  mergeAgentNodes,
  parseTaskEventBlock,
  seedAgents,
  taskEventsUrl,
  summarizeEvent,
  type CommandCenterState,
} from "@/lib/task-events";
import { registryAgents, taskEvent } from "./fixtures";

function apply(state: CommandCenterState, events: ReturnType<typeof taskEvent>[]): CommandCenterState {
  return events.reduce((current, event) => commandCenterReducer(current, { type: "event", event }), state);
}

describe("parseTaskEventBlock", () => {
  it("parses a task_event frame and ignores comments/heartbeats and other events", () => {
    const event = taskEvent({ type: "agent.started", agent_id: "negotiation", seq: 3 });
    const block = `event: task_event\ndata: ${JSON.stringify(event)}\n\n`;
    expect(parseTaskEventBlock(block)).toEqual(event);
    expect(parseTaskEventBlock(": heartbeat")).toBeNull();
    expect(parseTaskEventBlock("event: snapshot\ndata: {}")).toBeNull();
  });

  it("ignores comment/heartbeat and non-task_event frames", () => {
    expect(parseTaskEventBlock(": heartbeat")).toBeNull();
    expect(parseTaskEventBlock("")).toBeNull();
    expect(parseTaskEventBlock("event: snapshot\ndata: {}")).toBeNull();
  });

  it("parses a task_event frame that also carries id/retry fields", () => {
    const event = taskEvent({ type: "task.completed", seq: 9, payload: { total_tokens: 42 } });
    const block = `id: 9\nevent: task_event\ndata: ${JSON.stringify(event)}\nretry: 3000\n\n`;
    const parsed = parseTaskEventBlock(block);
    expect(parsed?.seq).toBe(9);
    expect(parsed?.payload.total_tokens).toBe(42);
  });
});

describe("taskEventsUrl", () => {
  it("uses the base (proxy) and the cursor, with no credentials", () => {
    expect(taskEventsUrl("/backend", "a b", 7)).toBe("/backend/api/tasks/a%20b/events?after=7");
  });
});

describe("commandCenterReducer", () => {
  it("keeps every agent idle until a real event says otherwise", () => {
    const seeded = seedAgents(initialState("run-1"), registryAgents().agents);
    for (const node of Object.values(seeded.agents)) {
      expect(node.status).toBe("idle");
    }
    // an agent.started for negotiation does not make the others busy
    const next = commandCenterReducer(seeded, {
      type: "event",
      event: taskEvent({ type: "agent.started", agent_id: "negotiation" }),
    });
    expect(next.agents.negotiation.status).toBe("running");
    expect(next.agents.collections.status).toBe("idle");
  });

  it("maps task lifecycle events onto the task view status", () => {
    const state = apply(
      initialState("run-1"),
      [
        taskEvent({ type: "task.created", seq: 1 }),
        taskEvent({ type: "task.started", seq: 2 }),
      ],
    );
    expect(state.taskStatus).toBe("running");
    expect(
      commandCenterReducer(state, { type: "event", event: taskEvent({ type: "task.completed", seq: 3 }) })
        .taskStatus,
    ).toBe("completed");
  });

  it("deduplicates by event_id across replay and live", () => {
    const dup = taskEvent({ type: "agent.progress", agent_id: "chat", seq: 5, event_id: "same-id" });
    let state = commandCenterReducer(initialState("run-1"), { type: "event", event: dup });
    const before = state.timeline.length;
    state = commandCenterReducer(state, { type: "event", event: dup });
    expect(state.timeline.length).toBe(before);
    expect(state.lastSeq).toBe(5);
  });

  it("never lets a stale task overwrite a newer selection", () => {
    let state = commandCenterReducer(initialState("new-task"), {
      type: "event",
      event: taskEvent({ type: "task.started", task_id: "new-task", seq: 1 }),
    });
    state = commandCenterReducer(state, {
      type: "event",
      event: taskEvent({ type: "task.completed", task_id: "old-task", seq: 2 }),
    });
    expect(state.taskStatus).toBe("running"); // the old task's completion is ignored
    expect(state.lastSeq).toBe(1);
  });

  it("tracks real tool activity and clears it on completion", () => {
    let state = commandCenterReducer(initialState("run-1"), {
      type: "event",
      event: taskEvent({ type: "tool.started", agent_id: "chat", seq: 1, payload: { tool: "get_revenue" } }),
    });
    expect(state.agents.chat.activeTools).toEqual(["get_revenue"]);
    expect(state.toolRunning.get_revenue).toBe("chat");
    state = commandCenterReducer(state, {
      type: "event",
      event: taskEvent({ type: "tool.completed", agent_id: "chat", seq: 2, payload: { tool: "get_revenue", ok: true } }),
    });
    expect(state.agents.chat.activeTools).toEqual([]);
    expect(state.toolRunning.get_revenue).toBeUndefined();
  });

  it("records agent completion counts from the payload, not invented ones", () => {
    const state = commandCenterReducer(initialState("run-1"), {
      type: "event",
      event: taskEvent({
        type: "agent.completed",
        agent_id: "collections",
        seq: 1,
        payload: { processed: 3, cached: 1, failed: 0, needs_review: 0, duration_ms: 120 },
      }),
    });
    expect(state.agents.collections.status).toBe("completed");
    expect(state.agents.collections.processed).toBe(3);
    expect(state.agents.collections.cached).toBe(1);
    expect(state.agents.collections.durationMs).toBe(120);
  });

  it("advances the cursor to the highest processed sequence", () => {
    const state = apply(
      initialState("run-1"),
      [
        taskEvent({ type: "task.created", seq: 1 }),
        taskEvent({ type: "agent.progress", agent_id: "negotiation", seq: 4 }),
        taskEvent({ type: "agent.progress", agent_id: "negotiation", seq: 2 }),
      ],
    );
    expect(state.lastSeq).toBe(4);
  });

  it("caps the timeline and keeps the details aligned", () => {
    let state = initialState("run-1");
    for (let index = 0; index < 260; index += 1) {
      state = commandCenterReducer(state, {
        type: "event",
        event: taskEvent({ type: "agent.progress", agent_id: "chat", seq: index + 1, payload: { index } }),
      });
    }
    expect(state.timeline.length).toBeLessThanOrEqual(200);
    // newest first
    expect(state.timeline[0].seq).toBe(260);
    expect(Object.keys(state.details).length).toBe(state.timeline.length);
  });
});

describe("execution state machine", () => {
  it("is idle with no turn and starting with nothing but a submission", () => {
    const base = { taskStatus: "idle" as const, hasAgentActivity: false, hasToolActivity: false };
    expect(deriveExecution({ ...base, turn: null })).toBe("idle");
    expect(
      deriveExecution({ ...base, turn: { phase: "starting", taskId: null } }),
    ).toBe("starting");
  });

  it("only promotes to running on real backend evidence", () => {
    const turn = { phase: "starting" as const, taskId: "task-1" };
    expect(deriveExecution({ turn, taskStatus: "created", hasAgentActivity: false, hasToolActivity: false }))
      .toBe("starting");
    expect(deriveExecution({ turn, taskStatus: "running", hasAgentActivity: false, hasToolActivity: false }))
      .toBe("running");
    expect(deriveExecution({ turn, taskStatus: "created", hasAgentActivity: false, hasToolActivity: true }))
      .toBe("running");
    expect(deriveExecution({ turn, taskStatus: "created", hasAgentActivity: true, hasToolActivity: false }))
      .toBe("running");
  });

  it("reads the streamed answer as synthesizing", () => {
    expect(
      deriveExecution({
        turn: { phase: "synthesizing", taskId: "task-1" },
        taskStatus: "running",
        hasAgentActivity: false,
        hasToolActivity: true,
      }),
    ).toBe("synthesizing");
  });

  it("never lets a completed or failed task look alive", () => {
    const turn = { phase: "starting" as const, taskId: "task-1" };
    // the chat stream never finished, yet the backend recorded the ending
    expect(deriveExecution({ turn, taskStatus: "completed", hasAgentActivity: true, hasToolActivity: false }))
      .toBe("completed");
    expect(deriveExecution({ turn, taskStatus: "failed", hasAgentActivity: true, hasToolActivity: true }))
      .toBe("failed");
    expect(deriveExecution({ turn, taskStatus: "cancelled", hasAgentActivity: true, hasToolActivity: false }))
      .toBe("cancelled");
    expect(isLivePhase("completed")).toBe(false);
    expect(isLivePhase("failed")).toBe(false);
    expect(isLivePhase("cancelled")).toBe(false);
    expect(isLivePhase("running")).toBe(true);
  });

  it("keeps an unknown persisted status from reading as finished work", () => {
    // "unknown" is the backend saying it has no terminal event: the turn is
    // still whatever the stream reported, never completed
    const phase = deriveExecution({
      turn: { phase: "starting", taskId: "task-1" },
      taskStatus: "unknown",
      hasAgentActivity: false,
      hasToolActivity: false,
    });
    expect(phase).toBe("starting");
  });

  it("settles only into a live phase while work is on screen", () => {
    expect(deriveExecution({ turn: { phase: "completed", taskId: "t" }, taskStatus: "completed", hasAgentActivity: true, hasToolActivity: false }))
      .toBe("completed");
  });
});

describe("reducer ordering and monotonicity", () => {
  it("orders the timeline by sequence even when events arrive out of order", () => {
    const state = apply(initialState("run-1"), [
      taskEvent({ type: "agent.progress", agent_id: "chat", seq: 7, event_id: "late" }),
      taskEvent({ type: "task.started", seq: 2, event_id: "a" }),
      taskEvent({ type: "agent.started", agent_id: "chat", seq: 5, event_id: "b" }),
      taskEvent({ type: "task.created", seq: 1, event_id: "c" }),
    ]);
    expect(state.timeline.map((entry) => entry.seq)).toEqual([7, 5, 2, 1]);
    expect(state.lastSeq).toBe(7);
  });

  it("never resurrects a finished task with a replayed earlier event", () => {
    const state = apply(initialState("run-1"), [
      taskEvent({ type: "task.created", seq: 1, event_id: "e1" }),
      taskEvent({ type: "task.started", seq: 2, event_id: "e2" }),
      taskEvent({ type: "task.completed", seq: 3, event_id: "e3" }),
      // a gap-filled replay arrives after the terminal event
      taskEvent({ type: "task.started", seq: 2, event_id: "e2-different-id" }),
    ]);
    expect(state.taskStatus).toBe("completed");
  });

  it("does not let a late agent.started un-complete an agent", () => {
    const state = apply(initialState("run-1"), [
      taskEvent({ type: "agent.completed", agent_id: "collections", seq: 4, event_id: "d4" }),
      taskEvent({ type: "agent.started", agent_id: "collections", seq: 2, event_id: "d2" }),
    ]);
    expect(state.agents.collections.status).toBe("completed");
  });

  it("shows a registry agent that finished another task as idle, not Done", () => {
    const doneElsewhere = registryAgents().agents.map((agent) => ({
      ...agent,
      status: "completed" as const,
      status_source: "events" as const,
    }));
    const nodes = mergeAgentNodes(doneElsewhere, {});
    expect(nodes.every((node) => node.status === "idle")).toBe(true);
    expect(nodes.every((node) => node.status !== "completed")).toBe(true);
  });

  it("keeps a genuinely in-flight registry agent visible", () => {
    const running = registryAgents().agents.map((agent) => ({
      ...agent,
      status: agent.id === "master" ? ("running" as const) : ("idle" as const),
      status_source: "events" as const,
    }));
    const nodes = mergeAgentNodes(running, {});
    expect(nodes.find((node) => node.id === "master")?.status).toBe("running");
    expect(nodes.find((node) => node.id === "chat")?.status).toBe("idle");
  });

  it("lets this task's own events win over the registry", () => {
    const running = registryAgents().agents.map((agent) => ({
      ...agent,
      status: "running" as const,
      status_source: "events" as const,
    }));
    const live = apply(initialState("run-1"), [
      taskEvent({ type: "agent.completed", agent_id: "chat", seq: 1, task_id: "run-1" }),
    ]);
    const nodes = mergeAgentNodes(running, live.agents);
    expect(nodes.find((node) => node.id === "chat")?.status).toBe("completed");
    // a task that never touched master still shows master's live registry state
    expect(nodes.find((node) => node.id === "master")?.status).toBe("running");
  });
});

describe("summarizeEvent", () => {
  it("describes cached reuse without claiming new work", () => {
    expect(summarizeEvent(taskEvent({ type: "agent.progress", payload: { cached: true } }))).toMatch(/cached/i);
  });

  it("shows a real tool name and failure", () => {
    expect(summarizeEvent(taskEvent({ type: "tool.started", payload: { tool: "get_invoices" } }))).toContain(
      "get_invoices",
    );
    expect(summarizeEvent(taskEvent({ type: "tool.completed", payload: { tool: "get_invoices", ok: false } }))).toMatch(
      /failed/,
    );
  });

  it("totals ERP sync new records from the payload", () => {
    const summary = summarizeEvent(
      taskEvent({ type: "erp.sync_completed", payload: { new_quotes: 3, new_orders: 2, new_payments: 1 } }),
    );
    expect(summary).toContain("6 new records");
  });

  it("never invents a percentage", () => {
    expect(summarizeEvent(taskEvent({ type: "task.started" }))).toBe("Task started");
  });
});
