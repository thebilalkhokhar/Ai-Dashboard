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

import { sendMessage } from "@/services/api";
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

    // the sidebar input is disabled and cannot create a second request
    fireEvent.change(inputs[1], { target: { value: "A second request" } });
    fireEvent.click(screen.getAllByLabelText("Send message")[1]);
    expect(sendMessage).toHaveBeenCalledTimes(1);

    await act(async () => {
      h.getHandler()!({
        event: "done",
        data: { text: "Lead L-1 moved from New to Contacted.", thread_id: "thread-1" },
      });
    });

    expect(screen.getAllByText("Lead L-1 moved from New to Contacted.")).toHaveLength(2);
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
