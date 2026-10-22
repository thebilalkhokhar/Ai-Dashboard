import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const h = vi.hoisted(() => {
  const streams: Array<{ taskId: string; onEvent: (event: unknown) => void }> = [];
  return { streams };
});

vi.mock("@/services/api", () => ({
  getChatThreadId: vi.fn(() => "test-thread"),
  setChatThreadId: vi.fn(),
  startNewChatThread: vi.fn(() => "test-thread-new"),
  fetchChatThread: vi.fn(async () => ({ thread_id: "test-thread", title: "", turns: [], updated_at: null })),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  },
  fetchTasks: vi.fn(),
  openTaskEventStream: vi.fn(
    (taskId: string, _after: number, options: { onEvent: (event: unknown) => void }) => {
      h.streams.push({ taskId, onEvent: options.onEvent });
      return new Promise<void>(() => {});
    },
  ),
}));

import { fetchTasks } from "@/services/api";
import { HistoryDrawer } from "@/components/command-center/history-drawer";
import { taskEvent, taskList } from "./fixtures";

beforeEach(() => {
  h.streams.length = 0;
  vi.mocked(fetchTasks).mockResolvedValue(taskList());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("HistoryDrawer", () => {
  it("lists human-readable requests and actual statuses", async () => {
    render(<HistoryDrawer open onOpenChange={vi.fn()} />);

    expect(await screen.findByText("Compare the last two lead pulls")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getByText("Daily business review")).toBeInTheDocument();
    expect(screen.getAllByText("Done")).toHaveLength(2);
    // the record that never reached a terminal event is not called "running"
    expect(screen.getByText("Outcome not recorded")).toBeInTheDocument();
    // the old identical chip list is gone
    expect(screen.queryByText("Agent analysis")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("run-new");
  });

  it("replays a selected task's persisted events and labels it as history", async () => {
    render(<HistoryDrawer open onOpenChange={vi.fn()} />);
    fireEvent.click(await screen.findByText("Compare the last two lead pulls"));

    await waitFor(() => expect(h.streams.some((stream) => stream.taskId === "run-new")).toBe(true));
    expect(screen.getByText("Recorded history")).toBeInTheDocument();
    expect(screen.getByText(/not a live execution/)).toBeInTheDocument();

    const stream = h.streams.find((item) => item.taskId === "run-new")!;
    await act(async () => {
      stream.onEvent(
        taskEvent({
          type: "task.completed",
          task_id: "run-new",
          agent_id: "chat",
          seq: 2,
        }),
      );
    });
    expect(await screen.findByText("Work finished")).toBeInTheDocument();
  });

  it("shows a loading state before the list arrives", async () => {
    let resolve!: (value: ReturnType<typeof taskList>) => void;
    vi.mocked(fetchTasks).mockReturnValue(new Promise((done) => {
      resolve = done;
    }));
    render(<HistoryDrawer open onOpenChange={vi.fn()} />);

    expect(screen.getByText("Loading execution history...")).toBeInTheDocument();
    await act(async () => resolve(taskList()));
    expect(await screen.findByText("Compare the last two lead pulls")).toBeInTheDocument();
  });

  it("shows an honest empty state", async () => {
    vi.mocked(fetchTasks).mockResolvedValue({ tasks: [] });
    render(<HistoryDrawer open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/No recorded executions yet/)).toBeInTheDocument();
  });

  it("shows an error with a retry that reloads the list", async () => {
    vi.mocked(fetchTasks).mockRejectedValueOnce(new Error("History is unavailable."));
    render(<HistoryDrawer open onOpenChange={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("History is unavailable.");
    fireEvent.click(screen.getByText("Try loading history again"));

    expect(await screen.findByText("Compare the last two lead pulls")).toBeInTheDocument();
    expect(fetchTasks).toHaveBeenCalledTimes(2);
  });

  it("reports only the duration, agents and failure kind the backend recorded", async () => {
    render(<HistoryDrawer open onOpenChange={vi.fn()} />);
    await screen.findByText("Compare the last two lead pulls");

    // a completed run: real duration and its participant count
    expect(screen.getByText("took 29 s")).toBeInTheDocument();
    expect(screen.getByText("2 agents")).toBeInTheDocument();

    // an unfinished run: no invented duration
    const running = screen.getByText("Compare the last two lead pulls")
      .closest("button") as HTMLElement;
    expect(running.textContent).not.toContain("took");

    vi.mocked(fetchTasks).mockResolvedValue({
      tasks: [
        {
          ...taskList().tasks[0],
          status: "failed" as const,
          duration_ms: 1500,
          agents: ["chat"],
          error_kind: "ConnectionError",
        },
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "Reload execution history" }));

    expect(await screen.findByText("stopped: ConnectionError")).toBeInTheDocument();
    expect(screen.getByText("took 1.5 s")).toBeInTheDocument();
    expect(screen.getByText("chat agent")).toBeInTheDocument();
  });

  it("names each real scheduler trigger, and never invents one", async () => {
    render(<HistoryDrawer open onOpenChange={vi.fn()} />);

    // the compound trigger the scheduler actually writes
    expect(await screen.findByText("New quote arrived + New lead arrived")).toBeInTheDocument();
    // an outcome the events never settled
    expect(screen.getByText("Outcome not recorded")).toBeInTheDocument();
    // an unknown trigger is shown as itself, not dressed up as a known review
    expect(screen.getByText("some future signal")).toBeInTheDocument();
    expect(screen.queryByText("Assistant review")).not.toBeInTheDocument();
  });

  it("closes on Escape and returns the request to the caller", async () => {
    const onOpenChange = vi.fn();
    render(<HistoryDrawer open onOpenChange={onOpenChange} />);
    await screen.findByText("Compare the last two lead pulls");

    fireEvent.keyDown(document.querySelector('[role="dialog"]') as HTMLElement, {
      key: "Escape",
    });

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("says so plainly when a replay has no recorded timing", async () => {
    vi.mocked(fetchTasks).mockResolvedValue({
      tasks: [{ ...taskList().tasks[0], created_at: null, duration_ms: null, agents: [] }],
    });
    render(<HistoryDrawer open onOpenChange={vi.fn()} />);
    fireEvent.click(await screen.findByText("Compare the last two lead pulls"));

    const replay = await screen.findByLabelText("Recorded task history");
    expect(within(replay).getByText("not recorded")).toBeInTheDocument();
    expect(within(replay).getByText("none recorded")).toBeInTheDocument();
  });
});
