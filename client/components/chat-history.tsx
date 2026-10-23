"use client";

// The Chats page: every conversation with the assistant, stored on the
// server and kept. Opening one shows it in a popup viewer right here -
// with Previous/Next navigation through the stored chats - so reading
// history never drags you away from the page. "Continue this chat"
// moves it into the Command Center and carries on that same thread;
// deleting removes the stored conversation (usage logs are kept
// server-side).

import { useCallback, useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  MessageSquareText,
  MessagesSquare,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import {
  deleteChatThread,
  fetchChatThreads,
  fetchChatThread,
  type ChatThreadDetail,
  type ChatThreadSummary,
} from "@/services/api";
import { useExecutionController } from "@/components/command-center/execution-context";

function formatWhen(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

export function ChatHistoryPanel({
  onOpenThread,
}: {
  onOpenThread: () => void;
}) {
  const { threadId: activeThreadId, openThread } = useExecutionController();
  const [chats, setChats] = useState<ChatThreadSummary[] | null>(null);
  const [error, setError] = useState("");
  const [confirmingId, setConfirmingId] = useState("");
  // The popup viewer: an index into the list (so Previous/Next is just
  // index arithmetic) plus the detail fetched for the chat at that index.
  const [viewingIndex, setViewingIndex] = useState<number | null>(null);
  const [detail, setDetail] = useState<ChatThreadDetail | null>(null);
  const [detailError, setDetailError] = useState("");

  const viewing = viewingIndex !== null ? (chats ?? [])[viewingIndex] ?? null : null;

  const load = useCallback(async () => {
    try {
      setChats(await fetchChatThreads());
      setError("");
    } catch {
      setError("Could not load the stored chats. Is the backend running?");
    }
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      const result = await fetchChatThreads()
        .then((value) => ({ ok: true as const, value }))
        .catch(() => ({ ok: false as const, value: [] as ChatThreadSummary[] }));
      if (!active) return;
      if (result.ok) {
        setChats(result.value);
        setError("");
      } else {
        setError("Could not load the stored chats. Is the backend running?");
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Fetch the viewed chat's turns whenever the popup moves to another chat.
  const viewingId = viewing?.thread_id ?? null;
  useEffect(() => {
    if (!viewingId) return;
    let active = true;
    void (async () => {
      const result = await fetchChatThread(viewingId)
        .then((value) => ({ ok: true as const, value }))
        .catch(() => ({ ok: false as const, value: null }));
      if (!active) return;
      if (result.ok) {
        setDetail(result.value);
        setDetailError("");
      } else {
        setDetailError("That conversation could not be loaded.");
      }
    })();
    return () => {
      active = false;
    };
  }, [viewingId]);

  // Escape closes the popup; arrow keys walk the list, like a photo viewer.
  useEffect(() => {
    if (viewingIndex === null) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setViewingIndex(null);
      if (event.key === "ArrowLeft") {
        setViewingIndex((index) =>
          index !== null && index > 0 ? index - 1 : index,
        );
      }
      if (event.key === "ArrowRight") {
        setViewingIndex((index) =>
          index !== null && chats && index < chats.length - 1 ? index + 1 : index,
        );
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewingIndex, chats]);

  async function remove(threadId: string) {
    if (confirmingId !== threadId) {
      setConfirmingId(threadId);
      return;
    }
    setConfirmingId("");
    try {
      await deleteChatThread(threadId);
      setChats((current) => (current ?? []).filter((chat) => chat.thread_id !== threadId));
    } catch {
      setError("That chat could not be deleted. Try again.");
    }
  }

  function continueInCommandCenter() {
    if (!viewing) return;
    openThread(viewing.thread_id);
    setViewingIndex(null);
    onOpenThread();
  }

  const total = (chats ?? []).length;

  return (
    <section aria-label="Stored chats" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Chats</h2>
          <p className="text-sm text-muted">
            Every conversation is stored and stays here. Open one to read it
            in place — and JARVIS can search past chats too: ask &ldquo;what
            did we discuss about&hellip;&rdquo;.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-muted transition-colors hover:border-accent/40 hover:text-accent"
        >
          <RefreshCw size={14} aria-hidden="true" />
          Refresh
        </button>
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}

      {chats === null && !error && <p className="text-sm text-muted">Loading stored chats…</p>}

      {chats !== null && chats.length === 0 && (
        <p className="text-sm text-muted">
          No stored chats yet. Start a conversation in the Command Center and
          it will be kept here.
        </p>
      )}

      <ul className="divide-y divide-border rounded-xl border border-border">
        {(chats ?? []).map((chat, index) => {
          const isActive = chat.thread_id === activeThreadId;
          return (
            <li
              key={chat.thread_id}
              className="flex flex-wrap items-center gap-3 px-4 py-3"
            >
              <MessageSquareText
                size={16}
                aria-hidden="true"
                className={isActive ? "text-accent" : "text-muted"}
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">
                  {chat.title || "Untitled conversation"}
                </p>
                <p className="text-xs text-muted">
                  {chat.turn_count} {chat.turn_count === 1 ? "exchange" : "exchanges"}
                  {chat.updated_at ? ` · ${formatWhen(chat.updated_at)}` : ""}
                  {isActive ? " · open now" : ""}
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setDetail(null);
                  setDetailError("");
                  setViewingIndex(index);
                }}
                className="rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent/40 hover:text-accent"
              >
                Open
              </button>
              <button
                type="button"
                onClick={() => void remove(chat.thread_id)}
                className={`inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                  confirmingId === chat.thread_id
                    ? "border-red-400/60 text-red-300"
                    : "border-border text-muted hover:border-red-400/40 hover:text-red-300"
                }`}
                aria-label={
                  confirmingId === chat.thread_id
                    ? "Confirm delete"
                    : `Delete chat ${chat.title}`
                }
              >
                <Trash2 size={13} aria-hidden="true" />
                {confirmingId === chat.thread_id ? "Confirm delete" : "Delete"}
              </button>
            </li>
          );
        })}
      </ul>

      {viewing && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setViewingIndex(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={viewing.title || "Stored conversation"}
            className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-2xl border border-border bg-[#0c0f15] shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start gap-3 border-b border-border px-5 py-4">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-foreground">
                  {viewing.title || "Untitled conversation"}
                </p>
                <p className="text-xs text-muted">
                  {viewing.turn_count}{" "}
                  {viewing.turn_count === 1 ? "exchange" : "exchanges"}
                  {viewing.updated_at ? ` · ${formatWhen(viewing.updated_at)}` : ""}
                  {` · chat ${viewingIndex! + 1} of ${total}`}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setViewingIndex(null)}
                aria-label="Close"
                className="rounded-lg border border-border p-1.5 text-muted transition-colors hover:border-accent/40 hover:text-accent"
              >
                <X size={15} aria-hidden="true" />
              </button>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
              {detailError && (
                <p role="alert" className="text-sm text-red-300">
                  {detailError}
                </p>
              )}
              {!detail && !detailError && (
                <p className="text-sm text-muted">Loading conversation…</p>
              )}
              {detail?.turns.map((turn, index) => (
                <div key={index} className="space-y-2">
                  <div className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-accent/15 px-3.5 py-2.5 text-sm text-foreground">
                    {turn.question}
                  </div>
                  <div className="mr-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-bl-md border border-border px-3.5 py-2.5 text-sm text-foreground">
                    {turn.answer}
                  </div>
                </div>
              ))}
              {detail && detail.turns.length === 0 && (
                <p className="text-sm text-muted">This conversation has no saved turns.</p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-3">
              <button
                type="button"
                disabled={viewingIndex === 0}
                onClick={() => setViewingIndex((index) => (index !== null && index > 0 ? index - 1 : index))}
                className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChevronLeft size={14} aria-hidden="true" />
                Previous chat
              </button>
              <button
                type="button"
                disabled={viewingIndex === total - 1}
                onClick={() => setViewingIndex((index) => (index !== null && index < total - 1 ? index + 1 : index))}
                className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next chat
                <ChevronRight size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={continueInCommandCenter}
                className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-accent/50 px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10"
              >
                <MessagesSquare size={14} aria-hidden="true" />
                Continue this chat
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
