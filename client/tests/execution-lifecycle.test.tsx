import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// Phase 2: the execution lifecycle, tested through the real controller so the
// guarantees are the ones the UI actually renders - not just the reducer's.

const h = vi.hoisted(() => {
  const streams: Array<{ taskId: string; onEvent: (e: unknown) => void }> = [];
  let sendHandler: ((event: unknown) => void) | null = null;
  let settle: ((value: unknown) => void) | null = null;
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }
  return {
    streams,
    ApiError,
    getSendHandler: () => sendHandler,
    setSendHandler: (handler: ((event: unknown) => void) | null) => {
      sendHandler = handler;
    },
    resolveSend: () => settle?.(undefined),
    setResolveSend: (fn: ((value: unknown) => void) | null) => {
      settle = fn;
    },
  };
});

vi.mock("@/services/api", () => ({
  ApiError: h.ApiError,
  fetchAgents: vi.fn(),
  fetchTasks: vi.fn(),
  openTaskEventStream: vi.fn(
    (taskId: string, _after: number, options: { onEvent: (e: unknown) => void }) => {
      h.streams.push({ taskId, onEvent: options.onEvent });
      return new Promise<void>(() => {});
    },
  ),
  sendMessage: vi.fn(
    (_message: string, onEvent: (event: unknown) => void) =>
      new Promise((resolve) => {
        h.setResolveSend(resolve);
        h.setSendHandler((event) => onEvent(event));
      }),
  ),
}));

import { fetchAgents, fetchTasks, sendMessage } from "@/services/api";
import { ChatInterface } from "@/components/chat-interface";
import { CommandCenter } from "@/components/command-center/command-center";
import { ExecutionOverlay } from "@/components/command-center/execution-overlay";
import {
  EXIT_TRANSITION_MS,
  ExecutionControllerProvider,
} from "@/components/command-center/execution-context";
import { registryAgents, taskEvent, taskList } from "./fixtures";

function renderApp() {
  return render(
    <ExecutionControllerProvider>
      <CommandCenter />
      <ExecutionOverlay />
    </ExecutionControllerProvider>,
  );
}

// The sidebar conversation has no workspace of its own, so the overlay is its
// active execution view.
function renderSidebarOnly() {
  return render(
    <ExecutionControllerProvider>
      <ChatInterface />
      <ExecutionOverlay />
    </ExecutionControllerProvider>,
  );
}

function submit(message: string) {
  fireEvent.change(screen.getByLabelText("Message Biztek Assistant"), {
    target: { value: message },
  });
  fireEvent.click(screen.getByLabelText("Send message"));
}

async function startTurn(message = "Show overdue accounts") {
  await act(async () => submit(message));
  await act(async () => {
    h.getSendHandler()!({ event: "task", data: { task_id: "task-live", thread_id: "thread-1" } });
  });
  await waitFor(() => expect(h.streams.some((s) => s.taskId === "task-live")).toBe(true));
  return h.streams.find((s) => s.taskId === "task-live")!;
}

async function emit(stream: { onEvent: (e: unknown) => void }, event: unknown) {
  await act(async () => stream.onEvent(event));
}

beforeEach(() => {
  h.streams.length = 0;
  h.setSendHandler(null);
  h.setResolveSend(null);
  vi.mocked(fetchAgents).mockResolvedValue(registryAgents());
  vi.mocked(fetchTasks).mockResolvedValue(taskList());
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("execution lifecycle", () => {
  it("walks idle -> starting -> running -> synthesizing -> completed", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("Master AI")).toBeInTheDocument());
    const status = () => screen.getByTestId("cc-workspace-status");

    // idle: the large hero orb owns the stage and there is no operation flow
    expect(screen.getByTestId("cc-idle-stage")).toBeInTheDocument();
    expect(screen.getByTestId("jarvis-orb")).toHaveClass("jo--hero");
    expect(screen.queryByTestId("cc-operation-flow")).not.toBeInTheDocument();

    await act(async () => submit("Show overdue accounts"));
    // starting: the flow replaces the hero stage, and nothing claims work happened
    expect(await screen.findByTestId("cc-operation-flow")).toBeInTheDocument();
    expect(screen.queryByTestId("cc-idle-stage")).not.toBeInTheDocument();
    // only the small route core is drawn, never the large idle orb again
    expect(screen.getAllByTestId("jarvis-orb")).toHaveLength(1);
    expect(screen.getByTestId("jarvis-orb")).toHaveClass("jo--core");
    expect(status().textContent).toBe("Executing");

    const stream = await startTurn();
    await emit(stream, taskEvent({ type: "task.created", task_id: "task-live", seq: 1 }));
    await emit(stream, taskEvent({ type: "task.started", task_id: "task-live", seq: 2, agent_id: "chat" }));
    await emit(
      stream,
      taskEvent({
        type: "tool.started",
        task_id: "task-live",
        seq: 3,
        agent_id: "chat",
        payload: { tool: "get_invoices" },
      }),
    );
    // running: driven by the real tool event, not by an animation
    expect(within(screen.getByTestId("flow-node-tool-get_invoices")).getByText("working")).toBeInTheDocument();

    await act(async () => {
      h.getSendHandler()!({ event: "token", data: { text: "There are " } });
    });
    // synthesizing: the answer is streaming back
    expect(screen.getByTestId("flow-node-synthesize")).toHaveTextContent("working");

    await act(async () => {
      h.getSendHandler()!({
        event: "done",
        data: { text: "There are 42 overdue invoices.", thread_id: "thread-1", task_id: "task-live" },
      });
    });
    // completed: the response is readable and the view is not "working"
    expect(await screen.findByText("There are 42 overdue invoices.")).toBeInTheDocument();
    expect(screen.getByTestId("flow-node-synthesize")).toHaveTextContent("done");
  });

  it("grows a routing tree: lookups appear under the specialist whose territory they belong to", async () => {
    renderApp();
    const stream = await startTurn("Show overdue invoices and stuck leads");
    await emit(stream, taskEvent({ type: "task.started", task_id: "task-live", seq: 1, agent_id: "chat" }));
    await emit(
      stream,
      taskEvent({ type: "tool.started", task_id: "task-live", seq: 2, agent_id: "chat", payload: { tool: "get_invoices" } }),
    );
    await emit(
      stream,
      taskEvent({ type: "tool.started", task_id: "task-live", seq: 3, agent_id: "chat", payload: { tool: "get_stuck_leads" } }),
    );

    // one branch per territory a real lookup touched, each carrying its leaf
    const collections = screen.getByTestId("flow-branch-collections");
    expect(within(collections).getByText("Collections Assistant")).toBeInTheDocument();
    expect(within(collections).getByText("working")).toBeInTheDocument();
    expect(within(collections).getByText(/routed by Chat Assistant/i)).toBeInTheDocument();
    const leads = screen.getByTestId("flow-branch-lead_followup");
    expect(within(leads).getByText("Lead Follow-up Assistant")).toBeInTheDocument();
    expect(screen.getByTestId("flow-node-tool-get_invoices")).toBeInTheDocument();
    expect(screen.getByTestId("flow-node-tool-get_stuck_leads")).toBeInTheDocument();
    // no lookup touched quotes, so no Negotiation branch is drawn
    expect(screen.queryByTestId("flow-branch-negotiation")).not.toBeInTheDocument();

    await emit(
      stream,
      taskEvent({ type: "tool.completed", task_id: "task-live", seq: 4, agent_id: "chat", payload: { tool: "get_invoices", ok: true } }),
    );
    // the branch settles only when its own lookup's completion event arrived
    expect(within(screen.getByTestId("flow-branch-collections")).getByText("done")).toBeInTheDocument();
    expect(within(screen.getByTestId("flow-branch-lead_followup")).getByText("working")).toBeInTheDocument();
  });

  it("never stays visually active when the backend records an ending it never streamed", async () => {
    renderApp();
    const stream = await startTurn();
    await emit(stream, taskEvent({ type: "task.started", task_id: "task-live", seq: 1, agent_id: "chat" }));
    expect(screen.getByText("Executing")).toBeInTheDocument();

    await emit(stream, taskEvent({ type: "task.failed", task_id: "task-live", seq: 2 }));

    // the chat stream never sent done, yet the view stops presenting work
    expect(screen.queryByText("Executing")).not.toBeInTheDocument();
    expect(screen.getByText("Needs attention")).toBeInTheDocument();
  });

  it("renders the active view once, never as a duplicate overlay", async () => {
    renderApp();
    await startTurn();
    expect(screen.getAllByTestId(/flow-node-/).length).toBeGreaterThan(0);
    // the Command Center owns the active view, so no second copy is mounted
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps backend work running when the view is dismissed", async () => {
    renderSidebarOnly();
    const stream = await startTurn();
    await emit(
      stream,
      taskEvent({
        type: "tool.started",
        task_id: "task-live",
        seq: 1,
        agent_id: "chat",
        payload: { tool: "get_invoices" },
      }),
    );

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    // hiding is not cancelling: no cancel request exists, and the promise is
    // still pending, so the answer can still arrive
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sendMessage).toHaveBeenCalledTimes(1);

    await act(async () => {
      h.getSendHandler()!({
        event: "done",
        data: { text: "There are 42 overdue invoices.", thread_id: "thread-1", task_id: "task-live" },
      });
      h.resolveSend();
    });
    expect(await screen.findByText("There are 42 overdue invoices.")).toBeInTheDocument();
  });

  it("does not present a finished turn as the current operation after it closes", async () => {
    renderApp();
    const stream = await startTurn();
    await emit(
      stream,
      taskEvent({
        type: "tool.completed",
        task_id: "task-live",
        seq: 1,
        agent_id: "chat",
        payload: { tool: "get_invoices", ok: true },
      }),
    );

    await act(async () => {
      h.getSendHandler()!({
        event: "done",
        data: { text: "There are 42 overdue invoices.", thread_id: "thread-1", task_id: "task-live" },
      });
      h.resolveSend();
    });

    // the workspace is idle again after its exit transition: the old prompt is
    // not left standing there as "Working on"
    await waitFor(
      () => {
        expect(screen.queryByTestId("cc-operation-flow")).not.toBeInTheDocument();
      },
      { timeout: EXIT_TRANSITION_MS + 1_500 },
    );
    expect(screen.getByTestId("cc-idle-stage")).toBeInTheDocument();
    expect(screen.getByText("Ready for a request")).toBeInTheDocument();
    const operation = screen.getByRole("region", { name: "Current operation" });
    expect(within(operation).getAllByText("Ready").length).toBeGreaterThan(0);
    expect(within(operation).queryByText("Show overdue accounts")).not.toBeInTheDocument();
    // the answer itself stays in the conversation
    expect(screen.getByText("There are 42 overdue invoices.")).toBeInTheDocument();
  });

  it("survives a replayed, duplicated and out-of-order stream without double work", async () => {
    renderApp();
    const stream = await startTurn();
    const started = taskEvent({
      type: "tool.started",
      task_id: "task-live",
      seq: 3,
      event_id: "stable-id",
      agent_id: "chat",
      payload: { tool: "get_invoices" },
    });

    await emit(stream, started);
    await emit(stream, started);
    await emit(stream, { ...started, seq: 1, event_id: "older-id" });

    const panel = screen.getByRole("region", { name: "Current operation" });
    // one node, one label, no duplicated activity rows
    expect(screen.getAllByTestId("flow-node-tool-get_invoices")).toHaveLength(1);
    expect(within(panel).getAllByText("Reviewing invoice details").length).toBeGreaterThan(0);
  });

  // §6: one assistant turn shows exactly one final response, even when the
  // backend carries the same text through both the streamed token frames and
  // the closing done frame. The old build doubled it; the reset/done contract
  // and the single-per-turn bubble must not.
  it("renders the fallback once when it is streamed and then completed", async () => {
    renderSidebarOnly();
    const fallback = "I couldn't verify that figure from the available data.";
    await startTurn("What did customers pay this week?");

    await act(async () => {
      h.getSendHandler()!({ event: "token", data: { text: "I couldn't " } });
    });
    await act(async () => {
      h.getSendHandler()!({ event: "token", data: { text: "verify that figure from the available data." } });
    });
    await act(async () => {
      h.getSendHandler()!({
        event: "done",
        data: { text: fallback, thread_id: "thread-1", task_id: "task-live" },
      });
      h.resolveSend();
    });

    // the streamed text is replaced by the done text in the one assistant bubble
    expect(await screen.findAllByText(fallback)).toHaveLength(1);
  });

  it("keeps two separate turns as two bubbles, one final response each", async () => {
    renderSidebarOnly();
    const fallback = "I couldn't verify that figure from the available data.";

    await startTurn("What did customers pay this week?");
    await act(async () => {
      h.getSendHandler()!({ event: "token", data: { text: fallback } });
    });
    await act(async () => {
      h.getSendHandler()!({
        event: "done",
        data: { text: fallback, thread_id: "thread-1", task_id: "task-live" },
      });
      h.resolveSend();
    });
    await waitFor(() => expect(screen.getAllByText(fallback)).toHaveLength(1));

    // a second, legitimate turn must add its own message, not replace or double
    await startTurn("How many quotes are open?");
    await act(async () => {
      h.getSendHandler()!({
        event: "done",
        data: { text: "There are 12 open quotes.", thread_id: "thread-1", task_id: "task-live" },
      });
      h.resolveSend();
    });

    await waitFor(() => expect(screen.getByText("There are 12 open quotes.")).toBeInTheDocument());
    // the earlier fallback is still shown exactly once; the two turns coexist
    expect(screen.getAllByText(fallback)).toHaveLength(1);
  });
});

