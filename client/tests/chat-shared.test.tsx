import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

const h = vi.hoisted(() => {
  let handler: ((event: unknown) => void) | null = null;
  return {
    getHandler: () => handler,
    setHandler: (next: ((event: unknown) => void) | null) => {
      handler = next;
    },
  };
});

vi.mock("@/services/api", () => ({
  getChatThreadId: vi.fn(() => "test-thread"),
  setChatThreadId: vi.fn(),
  startNewChatThread: vi.fn(() => "test-thread-new"),
  fetchChatThread: vi.fn(async () => ({ thread_id: "test-thread", title: "", turns: [], updated_at: null })),
  ApiError: class ApiError extends Error {},
  fetchAgents: vi.fn(async () => ({ agents: [] })),
  fetchTasks: vi.fn(async () => ({ tasks: [] })),
  openTaskEventStream: vi.fn(() => new Promise<void>(() => {})),
  sendMessage: vi.fn(
    (_message: string, onEvent: (event: unknown) => void) =>
      new Promise((resolve, reject) => {
        h.setHandler((event) => {
          onEvent(event);
          const name = (event as { event?: string })?.event;
          if (name === "done") resolve((event as { data: unknown }).data);
          if (name === "error") reject(new Error("stream error"));
        });
      }),
  ),
}));

import { fetchChatThread, sendMessage } from "@/services/api";
import { ChatInterface } from "@/components/chat-interface";
import { ExecutionControllerProvider } from "@/components/command-center/execution-context";

function renderBoth() {
  return render(
    <ExecutionControllerProvider>
      <section aria-label="Workspace chat">
        <ChatInterface />
      </section>
      <section aria-label="Sidebar chat">
        <ChatInterface />
      </section>
    </ExecutionControllerProvider>,
  );
}

beforeEach(() => {
  h.setHandler(null);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  window.sessionStorage.clear();
});

describe("shared chat controller", () => {
  it("shows one conversation in both entry points and submits only once", async () => {
    renderBoth();

    const inputs = screen.getAllByLabelText("Message Biztek Assistant");
    expect(inputs).toHaveLength(2);
    fireEvent.change(inputs[0], { target: { value: "Compare the last two lead pulls" } });
    fireEvent.click(screen.getAllByLabelText("Send message")[0]);

    expect(sendMessage).toHaveBeenCalledTimes(1);
    // the same message is visible in both views
    expect(screen.getAllByText("Compare the last two lead pulls")).toHaveLength(2);

    // the sidebar composer cannot create a second request mid-turn: its
    // message queues in the one shared conversation instead, visible in
    // both views
    fireEvent.change(inputs[1], { target: { value: "A second request" } });
    fireEvent.click(screen.getAllByLabelText("Send message")[1]);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText(/Queued: A second request/)).toHaveLength(2);

    await act(async () => {
      h.getHandler()!({
        event: "done",
        data: { text: "Lead L-1 moved from New to Contacted.", thread_id: "thread-1" },
      });
    });

    expect(screen.getAllByText("Lead L-1 moved from New to Contacted.")).toHaveLength(2);
  });

  it("persists the in-flight turn so an interrupted view can re-attach", async () => {
    renderBoth();
    fireEvent.change(screen.getAllByLabelText("Message Biztek Assistant")[0], {
      target: { value: "Who is waiting on WhatsApp?" },
    });
    fireEvent.click(screen.getAllByLabelText("Send message")[0]);

    await act(async () => {
      h.getHandler()!({ event: "task", data: { task_id: "task-777" } });
    });

    const raw = window.sessionStorage.getItem("biztek.active-turn.v1");
    expect(raw).toBeTruthy();
    const stored = JSON.parse(raw as string) as { taskId: string; prompt: string };
    expect(stored.taskId).toBe("task-777");
    expect(stored.prompt).toBe("Who is waiting on WhatsApp?");
  });

  it("re-attaches to a stored in-flight turn and fills its answer when it lands", async () => {
    vi.useFakeTimers();
    window.sessionStorage.setItem(
      "biztek.active-turn.v1",
      JSON.stringify({
        turnId: "turn-old",
        taskId: "task-777",
        prompt: "Who is waiting on WhatsApp?",
        threadId: "test-thread",
        startedAt: Date.now(),
      }),
    );
    const threadMock = vi.mocked(fetchChatThread);
    // seed load + recovery probe: the question exists, its answer not yet
    threadMock.mockResolvedValue({
      thread_id: "test-thread",
      title: "",
      turns: [{ question: "Who is waiting on WhatsApp?", answer: "" }],
      updated_at: null,
    } as never);

    render(
      <ExecutionControllerProvider>
        <ChatInterface />
      </ExecutionControllerProvider>,
    );
    await act(async () => {
      await Promise.resolve();
    });

    // the question is back on screen and the view is live again
    expect(screen.getByText("Who is waiting on WhatsApp?")).toBeTruthy();
    expect(screen.getByText("Working on your request")).toBeTruthy();

    // the completed turn lands in storage; the poll fills the answer in
    threadMock.mockResolvedValue({
      thread_id: "test-thread",
      title: "",
      turns: [{ question: "Who is waiting on WhatsApp?", answer: "Two customers are waiting." }],
      updated_at: null,
    } as never);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(screen.getByText("Two customers are waiting.")).toBeTruthy();
    expect(window.sessionStorage.getItem("biztek.active-turn.v1")).toBeNull();
  });

  it("never leaks a raw tool identifier into either conversation", async () => {
    renderBoth();
    fireEvent.change(screen.getAllByLabelText("Message Biztek Assistant")[0], {
      target: { value: "Show overdue accounts" },
    });
    fireEvent.click(screen.getAllByLabelText("Send message")[0]);

    await act(async () => {
      h.getHandler()!({ event: "tool", data: { name: "get_invoices" } });
    });

    expect(screen.getAllByText(/Reviewing invoice details/).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("get_invoices");
  });
});
