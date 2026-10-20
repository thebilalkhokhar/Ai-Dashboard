"use client";

import { useState, type FormEvent } from "react";
import { Bot, Send } from "lucide-react";
import { sendMessage } from "@/services/api";
import type { ChatStreamEvent } from "@/types/api";

type ChatEntry = { id: string; role: "user" | "assistant"; content: string };

function createMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function ChatInterface() {
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [message, setMessage] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [activeTool, setActiveTool] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submittedMessage = message.trim();
    if (!submittedMessage || isSending) return;

    const userMessageId = createMessageId();
    const assistantMessageId = createMessageId();
    setMessage("");
    setErrorMessage("");
    setMessages((currentMessages) => [
      ...currentMessages,
      { id: userMessageId, role: "user", content: submittedMessage },
      { id: assistantMessageId, role: "assistant", content: "" },
    ]);
    setIsSending(true);
    setActiveTool("");

    function handleStreamEvent(streamEvent: ChatStreamEvent) {
      if (streamEvent.event === "tool") {
        setActiveTool(streamEvent.data.name);
      } else if (streamEvent.event === "token") {
        setActiveTool("");
        setMessages((currentMessages) =>
          currentMessages.map((entry) =>
            entry.id === assistantMessageId
              ? { ...entry, content: entry.content + streamEvent.data.text }
              : entry,
          ),
        );
      } else if (streamEvent.event === "reset") {
        setActiveTool("");
        setMessages((currentMessages) =>
          currentMessages.map((entry) =>
            entry.id === assistantMessageId ? { ...entry, content: "" } : entry,
          ),
        );
      } else if (streamEvent.event === "done") {
        setActiveTool("");
        setMessages((currentMessages) =>
          currentMessages.map((entry) =>
            entry.id === assistantMessageId
              ? { ...entry, content: streamEvent.data.text }
              : entry,
          ),
        );
      } else {
        setErrorMessage(streamEvent.data.message);
      }
    }

    try {
      await sendMessage(submittedMessage, handleStreamEvent);
    } catch (error) {
      console.error("Chat interaction failed.", error);
      setErrorMessage(
        error instanceof Error ? error.message : "Message could not be sent. Please try again.",
      );
      setMessages((currentMessages) =>
        currentMessages.filter(
          (entry) => entry.id !== assistantMessageId || entry.content.length > 0,
        ),
      );
    } finally {
      setActiveTool("");
      setIsSending(false);
    }
  }

  return (
    <section aria-label="Biztek Assistant" className="flex h-full min-h-0 flex-col">
      <p className="flex shrink-0 items-center gap-2 px-5 py-3 text-xs text-muted">
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        {isSending ? "Thinking..." : "Ready"}
      </p>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-3" aria-live="polite">
        {messages.map((entry) =>
          entry.role === "user" ? (
            <div key={entry.id} className="ml-auto max-w-[85%] rounded-xl rounded-br-sm bg-surface-raised px-4 py-3 text-sm">
              {entry.content}
            </div>
          ) : (
            <div key={entry.id} className="flex max-w-[95%] gap-3">
              <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-surface-raised text-accent">
                <Bot size={15} aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p className="whitespace-pre-wrap text-sm leading-6 text-muted">{entry.content}</p>
              </div>
            </div>
          ),
        )}
        {isSending && !activeTool && messages[messages.length - 1]?.content === "" && (
          <p className="ml-10 text-sm text-muted" role="status">Waiting for assistant response...</p>
        )}
        {isSending && activeTool && (
          <p className="ml-10 text-xs text-muted" role="status">Agent is using: {activeTool}...</p>
        )}
      </div>

      <div className="shrink-0 border-t border-border p-4">
        <form onSubmit={handleSubmit} className="flex items-center gap-3 rounded-xl border border-border bg-surface-raised/70 px-3 py-2">
          <input
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Message Biztek Assistant..."
            aria-label="Message Biztek Assistant"
            disabled={isSending}
            className="min-w-0 flex-1 bg-transparent px-1 text-sm text-foreground outline-none placeholder:text-muted disabled:opacity-60"
          />
          <button
            type="submit"
            aria-label="Send message"
            disabled={isSending || message.trim().length === 0}
            className="grid h-8 w-8 place-items-center rounded-md bg-accent text-[#11150b] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send size={15} aria-hidden="true" />
          </button>
        </form>
        {errorMessage && <p className="mt-2 text-center text-xs text-red-400" role="alert">{errorMessage}</p>}
        <p className="mt-2 text-center text-[11px] text-muted">Connected to the Biztek assistant</p>
      </div>
    </section>
  );
}
