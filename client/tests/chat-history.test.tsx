import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const openThread = vi.fn();
const fetchChatThreads = vi.fn();
const deleteChatThread = vi.fn();

vi.mock("@/services/api", () => ({
  fetchChatThreads: () => fetchChatThreads(),
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

describe("ChatHistoryPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchChatThreads.mockResolvedValue(CHATS);
    deleteChatThread.mockResolvedValue(undefined);
  });

  it("lists every stored chat with its size, and marks the open one", async () => {
    render(<ChatHistoryPanel onOpenThread={() => {}} />);
    expect(await screen.findByText("Give me all details for Garcoa Inc.")).toBeTruthy();
    expect(screen.getByText(/3 exchanges/)).toBeTruthy();
    expect(screen.getByText(/open now/)).toBeTruthy();
  });

  it("opens a stored chat through the shared controller", async () => {
    const onOpenThread = vi.fn();
    render(<ChatHistoryPanel onOpenThread={onOpenThread} />);
    await screen.findByText("Give me all details for Garcoa Inc.");
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    expect(openThread).toHaveBeenCalledWith("t-1");
    expect(onOpenThread).toHaveBeenCalled();
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
