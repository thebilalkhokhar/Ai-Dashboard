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
  MessageSquare,
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
    <section aria-label="Stored chats" className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-border/80 bg-surface/80 p-5 shadow-sm backdrop-blur-md">
        <div className="max-w-2xl">
          <h2 className="text-base font-semibold tracking-tight text-foreground">Chats</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Every conversation is stored and stays here. Open one to read it
            in place — and JARVIS can search past chats too: ask &ldquo;what
            did we discuss about&hellip;&rdquo;.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-surface-raised/60 px-3.5 py-2 text-xs font-medium text-foreground/80 transition-colors hover:border-accent/40 hover:text-accent"
        >
          <RefreshCw size={13} aria-hidden="true" />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-negative-border bg-negative-muted px-4 py-3 text-xs text-negative" role="alert">
          {error}
        </div>
      )}

      {chats === null && !error && (
        <div className="flex items-center justify-center rounded-2xl border border-border/80 bg-surface/60 p-8 text-center">
          <p className="text-sm text-muted">Loading stored chats…</p>
        </div>
      )}

      {chats !== null && chats.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border/80 bg-surface/60 p-12 text-center">
          <div className="mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-surface-raised text-muted/70">
            <MessageSquare size={22} aria-hidden="true" />
          </div>
          <h3 className="text-sm font-semibold text-foreground">No conversations yet</h3>
          <p className="mt-1 max-w-sm text-xs text-muted">
            No stored chats yet. Start a conversation in the Command Center and
            it will be kept here.
          </p>
        </div>
      )}

      {chats !== null && chats.length > 0 && (
        <ul className="divide-y divide-border/60 overflow-hidden rounded-2xl border border-border/80 bg-surface/80 shadow-sm backdrop-blur-md">
          {chats.map((chat, index) => {
            const isActive = chat.thread_id === activeThreadId;
            return (
              <li
                key={chat.thread_id}
                className={`group flex flex-wrap items-center gap-4 px-5 py-4 transition-colors hover:bg-surface-raised/40 ${
                  isActive ? "bg-accent/[0.04]" : ""
                }`}
              >
                <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl border ${
                  isActive
                    ? "border-accent/40 bg-accent/15 text-accent"
                    : "border-border/60 bg-surface-raised text-muted group-hover:text-foreground group-hover:border-border transition-colors"
                }`}>
                  <MessageSquareText size={16} aria-hidden="true" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-foreground group-hover:text-accent transition-colors">
                    {chat.title || "Untitled conversation"}
                  </p>
                  <p className="mt-0.5 text-xs text-muted font-mono">
                    <span>{chat.turn_count} {chat.turn_count === 1 ? "exchange" : "exchanges"}</span>
                    {chat.updated_at ? <span> · {formatWhen(chat.updated_at)}</span> : null}
                    {isActive ? (
                      <span className="font-semibold text-accent"> · open now</span>
                    ) : null}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setDetail(null);
                      setDetailError("");
                      setViewingIndex(index);
                    }}
                    className="inline-flex items-center rounded-xl border border-border bg-surface-raised/60 px-3.5 py-1.5 text-xs font-semibold text-foreground/90 transition-colors hover:border-accent/40 hover:text-accent active:scale-[0.98]"
                  >
                    Open
                  </button>
                  <button
                    type="button"
                    onClick={() => void remove(chat.thread_id)}
                    className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition-all ${
                      confirmingId === chat.thread_id
                        ? "border-negative-border bg-negative-muted text-negative font-semibold shadow-sm"
                        : "border-border/70 text-muted hover:border-negative-border/60 hover:text-negative hover:bg-negative-muted/40"
                    }`}
                    aria-label={
                      confirmingId === chat.thread_id
                        ? "Confirm delete"
                        : `Delete chat ${chat.title}`
                    }
                  >
                    <Trash2 size={13} aria-hidden="true" />
                    <span>{confirmingId === chat.thread_id ? "Confirm delete" : "Delete"}</span>
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {viewing && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-in fade-in duration-200"
          onClick={() => setViewingIndex(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={viewing.title || "Stored conversation"}
            className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-2xl border border-border bg-surface shadow-2xl overflow-hidden"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start gap-3 border-b border-border/80 bg-surface-raised/50 px-6 py-4">
              <div className="min-w-0 flex-1">
                <p className="truncate text-base font-semibold text-foreground">
                  {viewing.title || "Untitled conversation"}
                </p>
                <p className="mt-0.5 text-xs text-muted font-mono">
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
                className="rounded-xl border border-border/80 bg-surface p-1.5 text-muted transition-colors hover:border-accent/40 hover:text-accent"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto p-6">
              {detailError && (
                <div role="alert" className="rounded-xl border border-negative-border bg-negative-muted p-3 text-xs text-negative">
                  {detailError}
                </div>
              )}
              {!detail && !detailError && (
                <div className="flex items-center justify-center py-12 text-xs text-muted">
                  Loading conversation…
                </div>
              )}
              {detail?.turns.map((turn, index) => (
                <div key={index} className="space-y-2.5">
                  <div className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm border border-accent/30 bg-accent/15 px-4 py-2.5 text-sm text-foreground shadow-sm">
                    {turn.question}
                  </div>
                  <div className="mr-auto w-fit max-w-[90%] whitespace-pre-wrap rounded-2xl rounded-bl-sm border border-border/80 bg-surface-raised/70 px-4 py-3 text-sm leading-relaxed text-foreground/95 shadow-sm">
                    {turn.answer}
                  </div>
                </div>
              ))}
              {detail && detail.turns.length === 0 && (
                <p className="py-8 text-center text-xs text-muted">This conversation has no saved turns.</p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-border/80 bg-surface-raised/40 px-6 py-3.5">
              <button
                type="button"
                disabled={viewingIndex === 0}
                onClick={() => setViewingIndex((index) => (index !== null && index > 0 ? index - 1 : index))}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChevronLeft size={14} aria-hidden="true" />
                Previous chat
              </button>
              <button
                type="button"
                disabled={viewingIndex === total - 1}
                onClick={() => setViewingIndex((index) => (index !== null && index < total - 1 ? index + 1 : index))}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next chat
                <ChevronRight size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={continueInCommandCenter}
                className="ml-auto inline-flex items-center gap-1.5 rounded-xl bg-accent px-4 py-1.5 text-xs font-semibold text-[#11150b] shadow-sm transition-all hover:opacity-90 active:scale-[0.98]"
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
