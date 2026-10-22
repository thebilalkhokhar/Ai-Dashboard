"use client";

// The one conversation experience.
//
// This component is a view over the shared execution controller: both the
// Command Center workspace and the assistant drawer render the same messages
// and submit through the same `send`, so there is exactly one conversation and
// exactly one in-flight request. No raw tool identifiers are shown.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Bot, Plus, RotateCcw, Send, Sparkles } from "lucide-react";
import { useExecutionController } from "@/components/command-center/execution-context";
import { ChatMarkdown } from "@/components/chat-markdown";
import { AnswerProvenance } from "@/components/chat-provenance";
import { answerEvidence, toolPhrase } from "@/lib/presentation";

const SUGGESTIONS = [
  "What did customers pay this week?",
  "Which invoices are overdue?",
  "Show me the leads that need follow-up.",
];

// How close to the bottom counts as "still following the answer". Beyond this
// the reader has scrolled up on purpose and must not be yanked back down.
const STICK_TO_BOTTOM_PX = 90;

export function ChatInterface() {
  const [message, setMessage] = useState("");
  const {
    messages,
    isSending,
    isLive,
    activeTool,
    chatError,
    send,
    retry,
    newChat,
    lastTurn,
    events,
    resultFrames,
  } = useExecutionController();

  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Follows new content until the reader scrolls up, then stays put until they
  // return to the bottom or ask another question.
  const stickRef = useRef(true);

  function trackScroll() {
    const element = scrollRef.current;
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    stickRef.current = distance <= STICK_TO_BOTTOM_PX;
  }

  useEffect(() => {
    const element = scrollRef.current;
    if (element && stickRef.current) element.scrollTop = element.scrollHeight;
  }, [messages, isSending]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // A new question is an explicit wish to see the answer, so re-attach.
    stickRef.current = true;
    if (send(message)) setMessage("");
  }

  const status =
    isSending || isLive
      ? "Working on your request"
      : lastTurn?.phase === "failed"
        ? "Needs attention"
        : "Ready";

  const lastMessageId = messages[messages.length - 1]?.id;
  // Provenance belongs to a finished answer whose own task events are loaded.
  // Anything else would be an assurance the browser cannot actually show.
  const settled =
    lastTurn
    && (lastTurn.phase === "completed" || lastTurn.phase === "failed")
    && lastTurn.taskId !== null
    && events.taskId === lastTurn.taskId
      ? lastTurn
      : null;
  const evidence = settled ? answerEvidence(events.timeline, events.details) : null;

  return (
    <section aria-label="Biztek Assistant" className="chat-panel">
      <div className="chat-head">
        <span className="chat-head-status">
          <span
            className={`h-1.5 w-1.5 rounded-full ${isSending || isLive ? "bg-cyan-300" : "bg-accent"}`}
            aria-hidden="true"
          />
          <span role="status">{status}</span>
        </span>
        {activeTool && (
          <span className="truncate text-[11px] text-cyan-200/80">{toolPhrase(activeTool)}</span>
        )}
        <button
          type="button"
          onClick={newChat}
          disabled={isSending}
          className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-50"
          title="Start a new conversation - this one stays stored under Chats"
        >
          <Plus size={12} aria-hidden="true" />
          New chat
        </button>
      </div>

      <div
        ref={scrollRef}
        onScroll={trackScroll}
        className="chat-scroll min-h-0 flex-1 overflow-y-auto px-4 py-3"
        aria-live="polite"
        aria-label="Conversation"
      >
        {messages.length === 0 && (
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <p className="flex items-center gap-2 text-sm font-medium text-foreground">
                <Sparkles size={14} className="text-cyan-300" aria-hidden="true" />
                Ask about the business
              </p>
              <p className="text-xs leading-5 text-muted">
                Payments, invoices, quotes, orders and leads. Answers use live ERP records and
                verified tool results.
              </p>
            </div>
            {/* Suggestions belong to the empty state only; once there is a
                conversation they would crowd out the messages. */}
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  disabled={isSending}
                  onClick={() => {
                    stickRef.current = true;
                    send(suggestion);
                  }}
                  className="rounded-full border border-border bg-surface-raised/60 px-3 py-1.5 text-left text-[11px] text-muted transition-colors hover:border-cyan-300/40 hover:text-foreground disabled:opacity-50"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((entry) =>
          entry.role === "user" ? (
            <div key={entry.id} className="chat-row chat-row--user">
              <div className="chat-bubble chat-bubble--user">{entry.content}</div>
            </div>
          ) : (
            <div key={entry.id} className="chat-row chat-row--assistant">
              <span className="chat-avatar" aria-hidden="true">
                <Bot size={15} />
              </span>
              <div className="chat-body">
                {entry.content ? (
                  <>
                    <div className="chat-bubble chat-bubble--assistant">
                      <ChatMarkdown text={entry.content} />
                      {isLive && entry.id === lastMessageId && (
                        <span className="chat-streaming" aria-hidden="true" />
                      )}
                    </div>
                    {entry.id === lastMessageId && evidence !== null && (
                      <AnswerProvenance evidence={evidence} frames={resultFrames} />
                    )}
                  </>
                ) : (
                  isSending && (
                    <div className="chat-bubble chat-bubble--assistant" role="status">
                      <span className="chat-typing" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                      <span className="sr-only">
                        {activeTool ? toolPhrase(activeTool) : "Thinking about your request"}
                      </span>
                    </div>
                  )
                )}
              </div>
            </div>
          ),
        )}
      </div>

      <div className="chat-foot shrink-0">
        <form
          onSubmit={handleSubmit}
          className="flex items-center gap-2 rounded-xl border border-border bg-surface-raised/60 px-2.5 py-1.5 focus-within:border-cyan-300/40"
        >
          <input
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Ask JARVIS about the business..."
            aria-label="Message Biztek Assistant"
            disabled={isSending}
            className="min-w-0 flex-1 bg-transparent px-1 text-[0.95rem] text-foreground outline-none placeholder:text-muted disabled:opacity-60"
          />
          <button
            type="submit"
            aria-label="Send message"
            disabled={isSending || message.trim().length === 0}
            className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-cyan-300 to-violet-400 text-[#081016] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Send size={15} aria-hidden="true" />
          </button>
        </form>

        {chatError && (
          <div className="mt-2 flex items-start justify-between gap-3" role="alert">
            <p className="text-xs leading-5 text-red-300">{chatError}</p>
            {lastTurn?.phase === "failed" && (
              <button
                type="button"
                onClick={retry}
                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted hover:text-foreground"
              >
                <RotateCcw size={11} aria-hidden="true" />
                Try again
              </button>
            )}
          </div>
        )}
        <p className="mt-1.5 text-center text-[10px] text-muted">
          Read-only assistant · verified against backend records
        </p>
      </div>
    </section>
  );
}
