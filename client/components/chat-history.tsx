"use client";

// The Chats page: every conversation with the assistant, stored on the
// server and kept. Opening one restores its saved turns in the Command
// Center and continues that same thread; deleting removes the stored
// conversation (usage logs are kept server-side).

import { useCallback, useEffect, useState } from "react";
import { MessageSquareText, RefreshCw, Trash2 } from "lucide-react";
import {
  deleteChatThread,
  fetchChatThreads,
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

  return (
    <section aria-label="Stored chats" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Chats</h2>
          <p className="text-sm text-muted">
            Every conversation is stored and stays here. Open one to continue
            it — and JARVIS can search past chats too: ask &ldquo;what did we
            discuss about&hellip;&rdquo;.
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
        {(chats ?? []).map((chat) => {
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
                  openThread(chat.thread_id);
                  onOpenThread();
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
    </section>
  );
}
