import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const h = vi.hoisted(() => {
  const streams: Array<{ taskId: string; onEvent: (e: unknown) => void }> = [];
  let sendHandler: ((event: unknown) => void) | null = null;
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
  };
});

vi.mock("@/services/api", () => ({
  getChatThreadId: vi.fn(() => "test-thread"),
  setChatThreadId: vi.fn(),
  startNewChatThread: vi.fn(() => "test-thread-new"),
  fetchChatThread: vi.fn(async () => ({ thread_id: "test-thread", title: "", turns: [], updated_at: null })),
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
      new Promise((resolve, reject) => {
        h.setSendHandler((event) => {
          onEvent(event);
          const name = (event as { event?: string })?.event;
          if (name === "error") {
            reject(new Error((event as { data: { message: string } }).data.message));
          } else if (name === "done") {
            resolve((event as { data: unknown }).data);
          }
        });
      }),
  ),
}));

import { fetchAgents, fetchTasks, sendMessage } from "@/services/api";
import { CommandCenter } from "@/components/command-center/command-center";
import { ExecutionControllerProvider } from "@/components/command-center/execution-context";
import { registryAgents, taskEvent, taskList } from "./fixtures";

function renderWorkspace() {
  return render(
    <ExecutionControllerProvider>
      <CommandCenter />
    </ExecutionControllerProvider>,
  );
}

function submit(message: string) {
  fireEvent.change(screen.getByLabelText("Message Biztek Assistant"), {
    target: { value: message },
  });
  fireEvent.click(screen.getByLabelText("Send message"));
}

beforeEach(() => {
  h.streams.length = 0;
  h.setSendHandler(null);
  vi.mocked(fetchAgents).mockResolvedValue(registryAgents());
  vi.mocked(fetchTasks).mockResolvedValue(taskList());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("CommandCenter workspace", () => {
  it("renders one conversation, the orb and the registered network without the old review panel", async () => {
    renderWorkspace();

    await waitFor(() => expect(screen.getByText("Master AI")).toBeInTheDocument());

    expect(screen.getByRole("img", { name: /AI core: Standing by/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Message Biztek Assistant")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open execution history" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Current operation" })).toBeInTheDocument();
    expect(screen.queryByText("Choose a request to review")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Ask the assistant$/)).not.toBeInTheDocument();
    // the sidebar chat is not rendered inside the main workspace
    expect(screen.getAllByLabelText("Message Biztek Assistant")).toHaveLength(1);
  });

  it("submits exactly once and drives the operation panel from real events", async () => {
    renderWorkspace();
    await waitFor(() => expect(screen.getByText("Master AI")).toBeInTheDocument());

    await act(async () => submit("Show overdue accounts"));
    expect(sendMessage).toHaveBeenCalledTimes(1);

    await act(async () => {
      h.getSendHandler()!({
        event: "task",
        data: { task_id: "task-live", thread_id: "thread-1" },
      });
    });
    await waitFor(() => expect(h.streams.some((stream) => stream.taskId === "task-live")).toBe(true));
    const stream = h.streams.find((item) => item.taskId === "task-live")!;

    await act(async () => {
      stream.onEvent(
        taskEvent({ type: "task.started", task_id: "task-live", agent_id: "chat", seq: 1 }),
      );
      stream.onEvent(
        taskEvent({ type: "agent.started", task_id: "task-live", agent_id: "chat", seq: 2 }),
      );
      stream.onEvent(
        taskEvent({
          type: "tool.started",
          task_id: "task-live",
          agent_id: "chat",
          seq: 3,
          payload: { tool: "get_invoices" },
        }),
      );
    });

    const operation = screen.getByRole("region", { name: "Current operation" });
    expect(within(operation).getAllByText("Reviewing invoice details").length).toBeGreaterThan(0);
    // the strip names the assistant without stealing the conversation's height
    expect(within(operation).getByText(/Assigned to Chat Assistant/)).toBeInTheDocument();
    expect(within(operation).getByRole("button", { name: /show detail/i })).toBeInTheDocument();
    // the user's own request is near the core and in the conversation
    expect(screen.getAllByText("Show overdue accounts").length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("task-live");

    // while the turn is in flight the input is disabled, so a second submit
    // cannot create a duplicate request
    await act(async () => submit("A second question"));
    expect(sendMessage).toHaveBeenCalledTimes(1);

    await act(async () => {
      h.getSendHandler()!({
        event: "done",
        data: { text: "There are 42 overdue invoices.", thread_id: "thread-1", task_id: "task-live" },
      });
    });
    expect(await screen.findByText("There are 42 overdue invoices.")).toBeInTheDocument();
    expect(within(operation).getAllByText("Response ready").length).toBeGreaterThan(0);

    // the finished answer shows the lookups that actually backed it
    const proof = await screen.findByTestId("chat-provenance");
    expect(within(proof).getByText("Reviewing invoice details")).toBeInTheDocument();
    expect(within(proof).getByText(/Checked against 1 backend lookup/)).toBeInTheDocument();

    // and the workspace no longer repeats the same status three times
    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading.textContent).toBe("Show overdue accounts");
    const meta = document.querySelector(".cc-visual-meta");
    expect(meta?.textContent).not.toMatch(/Assigned to/);
    // the card carries the request in full; it does not echo the phase label back
    const request = document.querySelector(".cc-request-text")?.textContent?.trim();
    const phase = document.querySelector(".cc-phase")?.textContent?.trim();
    expect(request).toBe("Show overdue accounts");
    expect(phase).toBe("Response ready");
  });

  it("ignores stale-task events and never shows internal identifiers", async () => {
    renderWorkspace();
    await waitFor(() => expect(screen.getByText("Master AI")).toBeInTheDocument());

    await act(async () => submit("Show overdue accounts"));
    await act(async () => {
      h.getSendHandler()!({
        event: "task",
        data: { task_id: "task-live", thread_id: "thread-1" },
      });
    });
    const stream = h.streams.find((item) => item.taskId === "task-live")!;

    await act(async () => {
      stream.onEvent(
        taskEvent({
          type: "tool.started",
          task_id: "someone-else",
          agent_id: "chat",
          seq: 1,
          payload: { tool: "get_invoices" },
        }),
      );
    });

    const operation = screen.getByRole("region", { name: "Current operation" });
    expect(within(operation).queryByText("Reviewing invoice details")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}/);
  });

  it("opens execution history, lists real requests and replays a selected task", async () => {
    renderWorkspace();
    await waitFor(() => expect(screen.getByText("Master AI")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Open execution history" }));

    expect(await screen.findByText("Compare the last two lead pulls")).toBeInTheDocument();
    expect(screen.getByText("Daily business review")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Compare the last two lead pulls"));

    await waitFor(() =>
      expect(h.streams.some((stream) => stream.taskId === "run-new")).toBe(true),
    );
    expect(screen.getByText("Recorded history")).toBeInTheDocument();
    expect(screen.getByText(/not a live execution/)).toBeInTheDocument();

    const stream = h.streams.find((item) => item.taskId === "run-new")!;
    await act(async () => {
      stream.onEvent(
        taskEvent({
          type: "tool.started",
          task_id: "run-new",
          agent_id: "chat",
          seq: 1,
          payload: { tool: "compare_lead_pulls" },
        }),
      );
    });

    expect(await screen.findByText("Comparing the last two lead pulls")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("run-new");
  });

  it("shows the totals and coverage the backend computed, not the model's summary", async () => {
    renderWorkspace();
    await waitFor(() => expect(screen.getByText("Master AI")).toBeInTheDocument());

    await act(async () => submit("What did customers pay this week?"));
    await act(async () => {
      h.getSendHandler()!({
        event: "task",
        data: { task_id: "task-pay", thread_id: "thread-1" },
      });
    });
    await waitFor(() => expect(h.streams.some((item) => item.taskId === "task-pay")).toBe(true));
    const stream = h.streams.find((item) => item.taskId === "task-pay")!;

    await act(async () => {
      stream.onEvent(
        taskEvent({
          type: "tool.completed",
          task_id: "task-pay",
          agent_id: "chat",
          seq: 2,
          payload: {
            tool: "get_payments",
            ok: true,
            duration_ms: 91,
            records_total: 67,
            records_returned: 10,
            records_remaining: 57,
            complete: false,
          },
        }),
      );
      h.getSendHandler()!({
        event: "tool_result",
        data: {
          tool: "get_payments",
          records_total: 67,
          records_returned: 10,
          records_remaining: 57,
          complete: false,
          money_label: "sum_amount",
          money_total: 290455.2,
          money_covers: "every matched payment record, not only the rows on this page",
          window: { date_from: "2026-10-19", date_to: "2026-10-22" },
        },
      });
    });

    await act(async () => {
      h.getSendHandler()!({
        event: "done",
        data: { text: "Customers paid $290,455.20.", thread_id: "thread-1", task_id: "task-pay" },
      });
    });

    const facts = await screen.findByTestId("chat-proof-facts");
    expect(within(facts).getByText("Verified total")).toBeInTheDocument();
    expect(within(facts).getByText(/covers every matched payment record/)).toBeInTheDocument();
    expect(within(facts).getByText("Date range")).toBeInTheDocument();
    expect(within(facts).getByText("2026-10-19 to 2026-10-22")).toBeInTheDocument();
    expect(within(facts).getByText("10 of 67 matched, 57 still to fetch")).toBeInTheDocument();
    // a partial page is never presented as the whole story
    expect(facts.textContent).not.toContain("all 67 matching records retrieved");

    // the chip detail carries the same stored counts from the durable event
    const proof = screen.getByTestId("chat-provenance");
    fireEvent.click(within(proof).getByRole("button", { name: /payment/i }));
    expect(
      within(proof).getByText(/10 of 67 matched records, 57 still to fetch/),
    ).toBeInTheDocument();
  });

  it("shows a verified total from a tool that returns no pages", async () => {
    renderWorkspace();
    await waitFor(() => expect(screen.getByText("Master AI")).toBeInTheDocument());

    await act(async () => submit("What did customers pay in the last 7 days?"));
    await act(async () => {
      h.getSendHandler()!({
        event: "task",
        data: { task_id: "task-rev", thread_id: "thread-1" },
      });
    });
    await waitFor(() => expect(h.streams.some((item) => item.taskId === "task-rev")).toBe(true));
    await act(async () => {
      h.streams.find((item) => item.taskId === "task-rev")!.onEvent(
        taskEvent({
          type: "tool.completed",
          task_id: "task-rev",
          agent_id: "chat",
          seq: 1,
          payload: { tool: "get_revenue", ok: true, duration_ms: 43, record_count: 54 },
        }),
      );
      h.getSendHandler()!({
        event: "tool_result",
        data: {
          tool: "get_revenue",
          record_count: 54,
          money_label: "total_payments",
          money_total: 323116,
          window: { date_from: "2026-10-16", date_to: "2026-10-22" },
        },
      });
      h.getSendHandler()!({
        event: "done",
        data: { text: "54 payments, $323,116.", thread_id: "thread-1", task_id: "task-rev" },
      });
    });

    const facts = await screen.findByTestId("chat-proof-facts");
    expect(within(facts).getByText("Total received $323,116 across 54 records")).toBeInTheDocument();
    expect(within(facts).getByText("2026-10-16 to 2026-10-22")).toBeInTheDocument();
    expect(within(facts).getByText("54 records totalled")).toBeInTheDocument();
  });

  it("omits the fact block when no lookup reported any", async () => {
    renderWorkspace();
    await waitFor(() => expect(screen.getByText("Master AI")).toBeInTheDocument());

    await act(async () => submit("Summarise the pipeline"));
    await act(async () => {
      h.getSendHandler()!({
        event: "task",
        data: { task_id: "task-bare", thread_id: "thread-1" },
      });
    });
    await waitFor(() => expect(h.streams.some((item) => item.taskId === "task-bare")).toBe(true));
    await act(async () => {
      h.streams.find((item) => item.taskId === "task-bare")!.onEvent(
        taskEvent({
          type: "tool.completed",
          task_id: "task-bare",
          agent_id: "chat",
          seq: 1,
          payload: { tool: "get_pipeline_overview", ok: true, duration_ms: 12 },
        }),
      );
      h.getSendHandler()!({
        event: "done",
        data: { text: "Here is the pipeline.", thread_id: "thread-1", task_id: "task-bare" },
      });
    });

    const proof = await screen.findByTestId("chat-provenance");
    expect(within(proof).getByText(/Checked against 1 backend lookup/)).toBeInTheDocument();
    expect(screen.queryByTestId("chat-proof-facts")).not.toBeInTheDocument();
  });
});
