import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const openThread = vi.fn();
const fetchChatThreads = vi.fn();
const fetchChatThread = vi.fn();
const deleteChatThread = vi.fn();

vi.mock("@/services/api", () => ({
  fetchChatThreads: () => fetchChatThreads(),
  fetchChatThread: (threadId: string) => fetchChatThread(threadId),
  deleteChatThread: (threadId: string) => deleteChatThread(threadId),
}));

vi.mock("@/components/command-center/execution-context", () => ({
  useExecutionController: () => ({ threadId: "t-2", openThread }),
}));

import { ChatHistoryPanel } from "@/components/chat-history";

const CHATS = [
  {
    thread_id: "t-1",
    title: "Give me all details for Garcoa Inc.",
    turn_count: 3,
    updated_at: "2026-10-22T10:00:00Z",
  },
  {
    thread_id: "t-2",
    title: "Which invoices are overdue?",
    turn_count: 1,
    updated_at: "2026-10-22T09:00:00Z",
  },
];

const DETAILS: Record<string, unknown> = {
  "t-1": {
    thread_id: "t-1",
    title: "Give me all details for Garcoa Inc.",
    turns: [
      { question: "Give me all details for Garcoa Inc.", answer: "Here is the profile.", created_at: "2026-10-22T10:00:00Z" },
      { question: "yes pull", answer: "Pulled the rest.", created_at: "2026-10-22T10:01:00Z" },
    ],
    updated_at: "2026-10-22T10:00:00Z",
  },
  "t-2": {
    thread_id: "t-2",
    title: "Which invoices are overdue?",
    turns: [
      { question: "Which invoices are overdue?", answer: "Three are overdue.", created_at: "2026-10-22T09:00:00Z" },
    ],
    updated_at: "2026-10-22T09:00:00Z",
  },
};

describe("ChatHistoryPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchChatThreads.mockResolvedValue(CHATS);
    fetchChatThread.mockImplementation(async (threadId: string) => DETAILS[threadId]);
    deleteChatThread.mockResolvedValue(undefined);
  });

  it("lists every stored chat with its size, and marks the open one", async () => {
    render(<ChatHistoryPanel onOpenThread={() => {}} />);
    expect(await screen.findByText("Give me all details for Garcoa Inc.")).toBeTruthy();
    expect(screen.getByText(/3 exchanges/)).toBeTruthy();
    expect(screen.getByText(/open now/)).toBeTruthy();
  });

  it("opens a stored chat in a popup, in place - no trip to the Command Center", async () => {
    const onOpenThread = vi.fn();
    render(<ChatHistoryPanel onOpenThread={onOpenThread} />);
    await screen.findByText("Give me all details for Garcoa Inc.");
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Here is the profile.")).toBeTruthy();
    expect(within(dialog).getByText("Pulled the rest.")).toBeTruthy();
    expect(within(dialog).getByText(/chat 1 of 2/)).toBeTruthy();
    // reading history stays on this page
    expect(openThread).not.toHaveBeenCalled();
    expect(onOpenThread).not.toHaveBeenCalled();
  });

  it("walks to the next and previous chat inside the popup", async () => {
    render(<ChatHistoryPanel onOpenThread={() => {}} />);
    await screen.findByText("Give me all details for Garcoa Inc.");
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: /Previous chat/ })).toHaveProperty("disabled", true);

    fireEvent.click(within(dialog).getByRole("button", { name: /Next chat/ }));
    expect(await within(dialog).findByText("Three are overdue.")).toBeTruthy();
    expect(within(dialog).getByText(/chat 2 of 2/)).toBeTruthy();
    expect(fetchChatThread).toHaveBeenCalledWith("t-2");

    fireEvent.click(within(dialog).getByRole("button", { name: /Previous chat/ }));
    expect(await within(dialog).findByText("Here is the profile.")).toBeTruthy();
  });

  it("continues the viewed chat in the Command Center only on request", async () => {
    const onOpenThread = vi.fn();
    render(<ChatHistoryPanel onOpenThread={onOpenThread} />);
    await screen.findByText("Give me all details for Garcoa Inc.");
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Continue this chat/ }));
    expect(openThread).toHaveBeenCalledWith("t-1");
    expect(onOpenThread).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes the popup with the Close button and with Escape", async () => {
    render(<ChatHistoryPanel onOpenThread={() => {}} />);
    await screen.findByText("Give me all details for Garcoa Inc.");
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    await screen.findByRole("dialog");
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("deletes only after a confirming second click", async () => {
    render(<ChatHistoryPanel onOpenThread={() => {}} />);
    await screen.findByText("Give me all details for Garcoa Inc.");
    fireEvent.click(screen.getAllByRole("button", { name: /Delete chat/ })[0]);
    expect(deleteChatThread).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(deleteChatThread).toHaveBeenCalledWith("t-1"));
    await waitFor(() =>
      expect(screen.queryByText("Give me all details for Garcoa Inc.")).toBeNull(),
    );
  });

  it("says plainly when nothing is stored yet", async () => {
    fetchChatThreads.mockResolvedValue([]);
    render(<ChatHistoryPanel onOpenThread={() => {}} />);
    expect(await screen.findByText(/No stored chats yet/)).toBeTruthy();
  });
});
