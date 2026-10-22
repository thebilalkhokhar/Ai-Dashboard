import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const h = vi.hoisted(() => {
  const taskStreams: Array<{ taskId: string; onEvent: (e: unknown) => void }> = [];
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }
  return {
    taskStreams,
    ApiError,
    emit: null as null | ((event: unknown) => void),
  };
});

vi.mock("@/services/api", () => ({
  ApiError: h.ApiError,
  fetchAgents: vi.fn(async () => ({ agents: [] })),
  fetchTasks: vi.fn(async () => ({ tasks: [] })),
  openTaskEventStream: vi.fn(
    (taskId: string, _after: number, options: { onEvent: (e: unknown) => void }) => {
      h.taskStreams.push({ taskId, onEvent: options.onEvent });
      return new Promise<void>(() => {}); // stays live
    },
  ),
  // the chat SSE transport: the test drives it by calling the captured handler,
  // which behaves like the real sendMessage (done resolves, error rejects)
  sendMessage: vi.fn(
    (_message: string, onEvent: (event: unknown) => void) =>
      new Promise((resolve, reject) => {
        h.emit = (event) => {
          onEvent(event);
          const name = (event as { event?: string })?.event;
          if (name === "error") {
            reject(new Error((event as { data: { message: string } }).data.message));
          } else if (name === "done") {
            resolve((event as { data: unknown }).data);
          }
        };
      }),
  ),
}));

import { sendMessage } from "@/services/api";
import { ChatInterface } from "@/components/chat-interface";
import {
  ExecutionControllerProvider,
  EXIT_TRANSITION_MS,
} from "@/components/command-center/execution-context";
import { ExecutionOverlay } from "@/components/command-center/execution-overlay";
import { taskEvent } from "./fixtures";

function renderApp() {
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

beforeEach(() => {
  h.taskStreams.length = 0;
  h.emit = null;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("chat -> execution overlay", () => {
  it("opens the overlay with the user's request as its title, not a task id", async () => {
    renderApp();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await act(async () => submit("What did customers pay this week?"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("What did customers pay this week?");
    expect(dialog.textContent).not.toMatch(/task-9|uuid|[0-9a-f]{8}-[0-9a-f]{4}/);
    // before the backend announces a task the flow only says it is receiving
    expect(within(dialog).getAllByText("Receiving your request").length).toBeGreaterThan(0);
    expect(within(dialog).getByTestId("overlay-operation-flow")).toBeInTheDocument();
    // the large idle orb is not repeated inside the active view
    expect(within(dialog).queryByTestId("jarvis-orb")).not.toBeInTheDocument();
  });

  it("subscribes to the task the backend announced and shows its real activity", async () => {
    renderApp();
    await act(async () => submit("Show overdue accounts"));

    await act(async () => {
      h.emit!({ event: "task", data: { task_id: "task-9", thread_id: "t-1" } });
    });

    await waitFor(() => expect(h.taskStreams).toHaveLength(1));
    expect(h.taskStreams[0].taskId).toBe("task-9");

    await act(async () => {
      h.taskStreams[0].onEvent(
        taskEvent({ type: "tool.started", task_id: "task-9", agent_id: "chat", seq: 1, payload: { tool: "get_invoices" } }),
      );
    });

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getAllByText("Reviewing invoice details").length).toBeGreaterThan(0);
    expect(within(dialog).getByText(/Assigned to Chat Assistant/)).toBeInTheDocument();
  });

  it("ignores events from a different task", async () => {
    renderApp();
    await act(async () => submit("Show overdue accounts"));
    await act(async () => {
      h.emit!({ event: "task", data: { task_id: "task-9", thread_id: "t-1" } });
    });
    await waitFor(() => expect(h.taskStreams).toHaveLength(1));

    await act(async () => {
      h.taskStreams[0].onEvent(
        taskEvent({ type: "tool.started", task_id: "someone-else", agent_id: "chat", seq: 1, payload: { tool: "get_invoices" } }),
      );
    });

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText("Reviewing invoice details")).not.toBeInTheDocument();
  });

  it("keeps the overlay open until the real response completes, then closes it", async () => {
    vi.useFakeTimers();
    renderApp();
    await act(async () => submit("Show overdue accounts"));
    await act(async () => {
      h.emit!({ event: "task", data: { task_id: "task-9", thread_id: "t-1" } });
    });
    // the work finishing is not enough on its own
    await act(async () => {
      h.taskStreams[0]?.onEvent(
        taskEvent({ type: "task.completed", task_id: "task-9", agent_id: "chat", seq: 2 }),
      );
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      h.emit!({ event: "done", data: { text: "$4,210 in total", thread_id: "t-1", task_id: "task-9" } });
    });

    // the answer is in the originating conversation
    expect(screen.getByText("$4,210 in total")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(EXIT_TRANSITION_MS + 50);
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("shows a calm failure and never a stack trace", async () => {
    renderApp();
    await act(async () => submit("Show overdue accounts"));

    await act(async () => {
      h.emit!({ event: "error", data: { message: "Chat limit reached. Try again in a minute." } });
    });

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Chat limit reached");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Chat limit reached");
    expect(dialog.textContent).not.toMatch(/Traceback|Error:/);
    // a failure stays visible with a recovery action instead of vanishing
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });

  it("cannot open a second overlay or send twice while a turn is running", async () => {
    renderApp();
    await act(async () => submit("First question"));
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(sendMessage).toHaveBeenCalledTimes(1);

    // the input is disabled mid-turn, so a second submit is a no-op
    await act(async () => submit("Second question"));
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("Escape hides the overlay without cancelling the chat", async () => {
    renderApp();
    await act(async () => submit("First question"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // the request is still in flight; its answer will still render in the chat
    await act(async () => {
      h.emit!({ event: "done", data: { text: "Still delivered", thread_id: "t-1", task_id: "task-9" } });
    });
    expect(screen.getByText("Still delivered")).toBeInTheDocument();
  });
});
