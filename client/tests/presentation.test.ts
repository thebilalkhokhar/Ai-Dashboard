import { describe, expect, it } from "vitest";

import {
  agentLabel,
  agentLabelFor,
  agentStatusWord,
  deriveOperation,
  deriveOrbState,
  answerEvidence,
  deriveExecutionTree,
  deriveStages,
  describeEvent,
  ORB_CAPTION,
  taskTitle,
  toolPhrase,
} from "@/lib/presentation";
import type { TimelineEntry } from "@/lib/task-events";
import { taskEvent } from "./fixtures";

function entry(over: Partial<TimelineEntry> & { seq: number }): TimelineEntry {
  return {
    event_id: `e${over.seq}`,
    ts: `2026-10-21T15:00:${String(over.seq % 60).padStart(2, "0")}Z`,
    type: "task.created",
    agent_id: "chat",
    summary: "",
    human: "",
    ...over,
  } as TimelineEntry;
}

describe("agent labels", () => {
  it("names the registered capabilities for an executive", () => {
    expect(agentLabelFor("master")).toBe("Master AI");
    expect(agentLabelFor("negotiation")).toBe("Negotiation Assistant");
    expect(agentLabelFor("collections")).toBe("Collections Assistant");
    expect(agentLabelFor("lead_followup")).toBe("Lead Follow-up Assistant");
    expect(agentLabelFor("chat")).toBe("Chat Assistant");
  });

  it("falls back to a neutral label instead of leaking an unknown id", () => {
    expect(agentLabel("some_internal_id")).toBe("Assistant");
    expect(agentLabel(null)).toBe("Assistant");
  });

  it("words a status without jargon", () => {
    expect(agentStatusWord("idle")).toBe("Ready");
    expect(agentStatusWord("running")).toBe("Working");
    expect(agentStatusWord("failed")).toBe("Unavailable");
  });
});

describe("tool phrases", () => {
  it("maps only real registered tools to specific claims", () => {
    expect(toolPhrase("get_payments")).toBe("Reviewing payment records");
    expect(toolPhrase("get_invoices")).toBe("Reviewing invoice details");
    expect(toolPhrase("get_stuck_leads")).toBe("Reviewing leads awaiting details");
  });

  it("uses a broad phrase for anything it cannot prove", () => {
    expect(toolPhrase("some_future_tool")).toBe("Reviewing business data");
    expect(toolPhrase(undefined)).toBe("Reviewing business data");
    // never claims an ERP write or a search it did not perform
    expect(toolPhrase("get_payments")).not.toMatch(/erp|write|send/i);
  });
});

describe("deriveOrbState", () => {
  const base = {
    execution: "idle" as const,
    hasTask: false,
    hasAgentActivity: false,
    hasToolActivity: false,
  };

  it("stands by when nothing is happening", () => {
    expect(deriveOrbState(base)).toBe("idle");
  });

  it("listens before the backend has announced a task", () => {
    expect(deriveOrbState({ ...base, execution: "starting" })).toBe("listening");
  });

  it("thinks while a task exists but no work has reported", () => {
    expect(deriveOrbState({ ...base, execution: "starting", hasTask: true })).toBe("thinking");
  });

  it("executes only when real agent or tool activity exists", () => {
    expect(deriveOrbState({ ...base, execution: "running", hasToolActivity: true })).toBe("executing");
    expect(deriveOrbState({ ...base, execution: "running", hasAgentActivity: true })).toBe("executing");
    // "running" is only reached from real events, so it is executing even if the
    // timeline has not been read yet
    expect(deriveOrbState({ ...base, execution: "running" })).toBe("executing");
    expect(deriveOrbState({ ...base, execution: "starting", hasTask: true, hasToolActivity: true })).toBe(
      "executing",
    );
  });

  it("responds when the answer is being written", () => {
    expect(deriveOrbState({ ...base, execution: "synthesizing" })).toBe("responding");
    expect(deriveOrbState({ ...base, execution: "completed", hasAgentActivity: true })).toBe("responding");
    expect(deriveOrbState({ ...base, execution: "completed" })).toBe("completed");
  });

  it("reads a failure as a failure", () => {
    expect(deriveOrbState({ ...base, execution: "failed" })).toBe("failed");
    expect(deriveOrbState({ ...base, execution: "cancelled" })).toBe("failed");
    expect(ORB_CAPTION.failed).toBeTruthy();
  });
});

describe("describeEvent", () => {
  it("phrases real events for a person, not as event names", () => {
    expect(describeEvent(taskEvent({ type: "task.created" }))).toBe("Request received");
    expect(describeEvent(taskEvent({ type: "agent.started", agent_id: "chat" }))).toBe(
      "Understanding your request",
    );
    expect(
      describeEvent(taskEvent({ type: "tool.started", payload: { tool: "get_revenue" } })),
    ).toBe("Reviewing payment totals");
    expect(describeEvent(taskEvent({ type: "agent.progress", payload: { cached: true } }))).toBe(
      "Reused a recent result",
    );
    expect(describeEvent(taskEvent({ type: "task.failed" }))).toBe("Something went wrong");
  });

  it("never invents a percentage or an ETA", () => {
    for (const type of [
      "task.started",
      "agent.started",
      "agent.progress",
      "tool.completed",
      "erp.sync_completed",
    ] as const) {
      const text = describeEvent(taskEvent({ type }));
      expect(text).not.toMatch(/%/);
      expect(text.toLowerCase()).not.toContain("estimated");
    }
  });

  it("falls back to a generic phrase for an unknown shape", () => {
    expect(describeEvent({ ...taskEvent({ type: "agent.progress" }), type: "agent.progress", payload: {} })).toBe(
      "Assistant is working",
    );
  });
});

describe("taskTitle", () => {
  it("uses the user's own words", () => {
    expect(taskTitle("What did Polkadot pay this month?")).toBe("What did Polkadot pay this month?");
  });

  it("takes the first sentence and clips long requests", () => {
    expect(taskTitle("Show overdue accounts. Then email nobody.")).toBe("Show overdue accounts.");
    expect(taskTitle("x".repeat(200)).length).toBeLessThanOrEqual(72);
  });

  it("stays neutral when the request is unavailable", () => {
    expect(taskTitle(null)).toBe("Assistant request");
    expect(taskTitle("   ")).toBe("Assistant request");
  });
});

describe("deriveOperation", () => {
  const base = {
    execution: "idle" as const,
    timeline: [],
    agents: {},
  };

  it("stays ready and idle with no turn", () => {
    const view = deriveOperation({ ...base, turn: null });
    expect(view.phase).toBe("Ready");
    expect(view.tone).toBe("idle");
    expect(view.agent).toBeNull();
  });

  it("says it is receiving the request before any evidence exists", () => {
    const view = deriveOperation({
      ...base,
      execution: "starting",
      turn: { prompt: "Show overdue accounts", taskId: null, message: "" },
    });
    expect(view.phase).toBe("Receiving your request");
    expect(view.evidence).toBeNull();
  });

  it("uses the real agent assignment and tool phrase", () => {
    const assigned = deriveOperation({
      ...base,
      execution: "running",
      turn: { prompt: "Review invoices", taskId: "t", message: "" },
      timeline: [
        {
          event_id: "e2",
          seq: 2,
          ts: "2026-10-21T15:00:02Z",
          type: "tool.started",
          agent_id: "chat",
          summary: "Tool get_invoices started",
          human: "Reviewing invoice details",
          toolName: "get_invoices",
        },
        {
          event_id: "e1",
          seq: 1,
          ts: "2026-10-21T15:00:01Z",
          type: "agent.started",
          agent_id: "chat",
          summary: "Started",
          human: "Understanding your request",
        },
      ],
    });
    expect(assigned.phase).toBe("Reviewing invoice details");
    expect(assigned.agent).toBe("Chat Assistant");
    expect(assigned.evidence).toBe("tool.started");
  });

  it("maps the machine's settled phases to their own captions", () => {
    const turn = { prompt: "Review invoices", taskId: "t", message: "" };
    expect(deriveOperation({ ...base, execution: "synthesizing", turn }).phase).toBe(
      "Preparing your report",
    );
    expect(deriveOperation({ ...base, execution: "completed", turn }).phase).toBe("Response ready");
    const failed = deriveOperation({
      ...base,
      execution: "failed",
      turn: { ...turn, message: "The daily limit was reached." },
    });
    expect(failed.phase).toBe("Couldn't complete that");
    expect(failed.detail).toBe("The daily limit was reached.");
    expect(deriveOperation({ ...base, execution: "cancelled", turn }).phase).toBe("Request stopped");
  });

  it("never invents a percentage or a stage the event does not support", () => {
    const view = deriveOperation({
      ...base,
      execution: "running",
      turn: { prompt: "Review invoices", taskId: "t", message: "" },
      timeline: [
        {
          event_id: "e1",
          seq: 1,
          ts: "2026-10-21T15:00:01Z",
          type: "agent.completed",
          agent_id: "collections",
          summary: "Completed",
          human: "Collections Assistant finished",
        },
      ],
    });
    expect(view.phase).toContain("finished");
    expect(view.phase).not.toMatch(/%/);
    expect(view.detail.toLowerCase()).not.toContain("estimated");
  });
});

describe("deriveStages", () => {
  it("draws only the steps the task really recorded", () => {
    const stages = deriveStages({
      execution: "running",
      agents: {},
      timeline: [
        entry({ seq: 5, type: "tool.completed", toolName: "get_payments" }),
        entry({ seq: 4, type: "tool.started", toolName: "get_payments" }),
        entry({ seq: 3, type: "agent.started", agent_id: "collections" }),
        entry({ seq: 2, type: "task.started" }),
        entry({ seq: 1, type: "task.created" }),
      ],
    });

    expect(stages.map((stage) => stage.id)).toEqual([
      "intake",
      "agent-collections",
      "tool-get_payments",
      "synthesize",
    ]);
    expect(stages[0].state).toBe("done");
    expect(stages[1].state).toBe("active");
    expect(stages[2].state).toBe("done");
    // nothing has streamed the answer yet, so synthesis is honestly pending
    expect(stages[3].state).toBe("pending");
  });

  it("adds no tool node for a tool that was never invoked", () => {
    const stages = deriveStages({
      execution: "starting",
      agents: {},
      timeline: [entry({ seq: 1, type: "task.created" })],
    });
    expect(stages.map((stage) => stage.id)).toEqual(["intake", "synthesize"]);
    expect(stages[0].state).toBe("done");
  });

  it("marks a failed lookup as failed, not as working", () => {
    const stages = deriveStages({
      execution: "running",
      agents: {},
      timeline: [
        entry({ seq: 3, type: "tool.completed", toolName: "get_payments", toolOk: false }),
        entry({ seq: 2, type: "tool.started", toolName: "get_payments" }),
      ],
    });
    expect(stages.find((stage) => stage.id === "tool-get_payments")?.state).toBe("failed");
  });

  it("counts repeated calls of one tool into one node", () => {
    const stages = deriveStages({
      execution: "running",
      agents: {},
      timeline: [
        entry({ seq: 4, type: "tool.started", toolName: "get_payments" }),
        entry({ seq: 3, type: "tool.completed", toolName: "get_payments" }),
        entry({ seq: 2, type: "tool.started", toolName: "get_payments" }),
        entry({ seq: 1, type: "task.started" }),
      ],
    });
    expect(stages.filter((stage) => stage.id === "tool-get_payments")).toHaveLength(1);
  });

  it("never shows a response stage as done before the answer finished", () => {
    const running = deriveStages({ execution: "running", agents: {}, timeline: [] });
    expect(running[running.length - 1].state).toBe("pending");
    const done = deriveStages({ execution: "completed", agents: {}, timeline: [] });
    expect(done[done.length - 1].state).toBe("done");
  });

  it("names the head and the tail of the route differently", () => {
    const chat = deriveStages({ execution: "running", agents: {}, timeline: [] });
    expect(chat[0].label).toBe("Chat Assistant");
    expect(chat[chat.length - 1].label).toBe("Response");

    // a batch run really did hand off to the master stage
    const withMaster = deriveStages({
      execution: "running",
      agents: {},
      timeline: [entry({ seq: 1, type: "agent.started", agent_id: "master" })],
    });
    expect(withMaster[withMaster.length - 1].label).toBe("Master AI");
  });

  it("keeps the stage list bounded when many tools ran", () => {
    const timeline = Array.from({ length: 12 }, (_, index) =>
      entry({ seq: index + 1, type: "tool.started", toolName: `tool_${index}` }),
    );
    const stages = deriveStages({ execution: "running", agents: {}, timeline });
    expect(stages.length).toBeLessThanOrEqual(7);
    expect(stages.some((stage) => stage.id === "tool-more")).toBe(true);
  });
});

describe("answerEvidence", () => {
  it("pairs each lookup with its own completion event", () => {
    const evidence = answerEvidence([
      entry({
        seq: 4,
        type: "tool.completed",
        toolName: "get_invoices",
        toolOk: true,
        durationMs: 719,
      }),
      entry({ seq: 3, type: "tool.started", toolName: "get_invoices" }),
      entry({
        seq: 2,
        type: "tool.completed",
        toolName: "get_payments",
        toolOk: true,
        durationMs: 1200,
      }),
      entry({ seq: 1, type: "tool.started", toolName: "get_payments" }),
    ]);

    // oldest first, so the chips read in the order the calls ran
    expect(evidence.map((item) => item.tool)).toEqual(["get_payments", "get_invoices"]);
    expect(evidence[0].durationMs).toBe(1200);
    expect(evidence[1].label).toBe("Reviewing invoice details");
  });

  it("marks a lookup that did not complete, and one still running", () => {
    const evidence = answerEvidence([
      entry({ seq: 3, type: "tool.completed", toolName: "get_leads", toolOk: false }),
      entry({ seq: 2, type: "tool.started", toolName: "get_leads" }),
      entry({ seq: 1, type: "tool.started", toolName: "get_payments" }),
    ]);
    const leads = evidence.find((item) => item.tool === "get_leads");
    const payments = evidence.find((item) => item.tool === "get_payments");
    expect(leads?.ok).toBe(false);
    expect(payments?.ok).toBe(true);
    expect(payments?.durationMs).toBeNull();
  });

  it("finds no evidence in a timeline with no tool events", () => {
    expect(answerEvidence([entry({ seq: 1, type: "task.started" })])).toEqual([]);
  });

  it("reads the record counts the backend stored in the event payload", () => {
    const evidence = answerEvidence(
      [
        entry({ seq: 2, type: "tool.started", toolName: "get_payments" }),
        entry({
          seq: 3,
          type: "tool.completed",
          toolName: "get_payments",
          toolOk: true,
          durationMs: 88,
        }),
      ],
      {
        e3: {
          tool: "get_payments",
          ok: true,
          duration_ms: 88,
          records_total: 67,
          records_returned: 10,
          records_remaining: 57,
          complete: false,
        },
      },
    );

    expect(evidence[0].records).toEqual({
      total: 67,
      returned: 10,
      remaining: 57,
      complete: false,
    });
  });

  it("leaves counts absent when the tool stored none, rather than implying zero", () => {
    const evidence = answerEvidence(
      [
        entry({ seq: 1, type: "tool.completed", toolName: "get_leads", toolOk: true }),
      ],
      { e1: { tool: "get_leads", ok: true, duration_ms: 4 } },
    );
    expect(evidence[0].records).toBeUndefined();
  });

  it("treats a missing completeness flag as not complete", () => {
    const evidence = answerEvidence(
      [entry({ seq: 1, type: "tool.completed", toolName: "get_invoices", toolOk: true })],
      { e1: { records_total: 5, records_returned: 5 } },
    );
    expect(evidence[0].records?.complete).toBe(false);
  });
});

describe("deriveExecutionTree", () => {
  it("groups lookups under their territory and hangs the real data stages off the leaf", () => {
    const timeline = [
      entry({ seq: 7, type: "chat.validation_completed" }),
      entry({ seq: 6, type: "chat.validation_started" }),
      entry({ seq: 5, type: "data.aggregation_completed", toolName: "get_payments" }),
      entry({ seq: 4, type: "data.filter_completed", toolName: "get_payments" }),
      entry({ seq: 3, type: "tool.completed", toolName: "get_payments", toolOk: true, durationMs: 617 }),
      entry({ seq: 2, type: "tool.started", toolName: "get_payments" }),
      entry({ seq: 1, type: "task.started" }),
    ];
    const details = {
      e3: { records_total: 25, records_returned: 10, records_remaining: 15, complete: false },
      e4: { matched: 25, returned: 10, remaining: 15, complete: false },
      e5: { aggregate: "sum_amount" },
      e7: { outcome: "passed" },
    };
    const tree = deriveExecutionTree({ execution: "completed", agents: {}, timeline, details });

    expect(tree.branches.map((branch) => branch.agentId)).toEqual(["collections"]);
    expect(tree.branches[0].state).toBe("done");
    const tool = tree.branches[0].tools[0];
    expect(tool.counts).toEqual({ total: 25, returned: 10, remaining: 15, complete: false });
    expect(tool.filter).toEqual({ matched: 25, returned: 10, remaining: 15, complete: false });
    expect(tool.aggregation).toEqual({ aggregate: "sum_amount" });
    expect(tool.durationMs).toBe(617);
    expect(tree.validation).toEqual({ state: "done", outcome: "passed" });
    expect(tree.directTools).toEqual([]);
  });

  it("invents no filter, aggregation, validation or branch a task never recorded", () => {
    const tree = deriveExecutionTree({
      execution: "running",
      agents: {},
      timeline: [
        entry({ seq: 2, type: "tool.started", toolName: "describe_table" }),
        entry({ seq: 1, type: "task.started" }),
      ],
      details: {},
    });
    expect(tree.branches).toEqual([]);
    expect(tree.directTools).toHaveLength(1);
    expect(tree.directTools[0].filter).toBeNull();
    expect(tree.directTools[0].aggregation).toBeNull();
    expect(tree.directTools[0].counts).toBeNull();
    expect(tree.validation).toBeNull();
  });

  it("tracks the validation stage through its real states", () => {
    const started = [entry({ seq: 1, type: "chat.validation_started" })];
    expect(
      deriveExecutionTree({ execution: "running", agents: {}, timeline: started, details: {} }).validation,
    ).toEqual({ state: "active", outcome: null });
    expect(
      deriveExecutionTree({ execution: "failed", agents: {}, timeline: started, details: {} }).validation,
    ).toEqual({ state: "failed", outcome: null });
  });

  it("phrases the new lifecycle events for people", () => {
    expect(describeEvent(taskEvent({ type: "data.filter_completed" }))).toBe("Filtered the matched records");
    expect(describeEvent(taskEvent({ type: "chat.response_started" }))).toBe("Writing the response");
    expect(
      describeEvent(taskEvent({ type: "chat.validation_completed", payload: { outcome: "replaced" } })),
    ).toBe("Answer rebuilt from verified figures");
    expect(
      describeEvent(taskEvent({ type: "chat.validation_completed", payload: { outcome: "passed" } })),
    ).toBe("Answer verified against the records");
  });
});

describe("deriveExecutionTree pagination", () => {
  it("shows a pagination stage only when the backend really walked pages", () => {
    const timeline = [
      entry({ seq: 4, type: "tool.completed", toolName: "get_payments", toolOk: true, durationMs: 40 }),
      entry({ seq: 3, type: "tool.completed", toolName: "get_payments", toolOk: true, durationMs: 55 }),
      entry({ seq: 2, type: "tool.started", toolName: "get_payments" }),
      entry({ seq: 1, type: "task.started" }),
    ];
    const details = {
      e3: { records_total: 60, records_returned: 30, records_remaining: 30, complete: false, offset: 0 },
      e4: { records_total: 60, records_returned: 30, records_remaining: 0, complete: true, offset: 30 },
    };
    const tree = deriveExecutionTree({ execution: "completed", agents: {}, timeline, details });
    const leaf = tree.branches.flatMap((branch) => branch.tools).find((tool) => tool.tool === "get_payments")
      ?? tree.directTools.find((tool) => tool.tool === "get_payments");
    expect(leaf?.pagination).toEqual({ pages: 2, lastOffset: 30 });

    const single = deriveExecutionTree({
      execution: "completed",
      agents: {},
      timeline: timeline.slice(1),
      details: { e3: details.e3 },
    });
    const singleLeaf = single.branches.flatMap((branch) => branch.tools).find((tool) => tool.tool === "get_payments")
      ?? single.directTools.find((tool) => tool.tool === "get_payments");
    expect(singleLeaf?.pagination).toBeNull();
  });
});
