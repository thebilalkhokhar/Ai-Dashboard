"use client";

// The one conversation experience.
//
// This component is a view over the shared execution controller: both the
// Command Center workspace and the assistant drawer render the same messages
// and submit through the same `send`, so there is exactly one conversation and
// exactly one in-flight request. No raw tool identifiers are shown.

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Plus, Reply, RotateCcw, Send, Sparkles, Square, X } from "lucide-react";
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

// A reply travels as a quote line ahead of the new text, so the backend sees
// the context with no new field - and the sender's own bubble renders the
// quote as a block instead of raw "> " text.
function splitQuote(content: string): { quote: string; body: string } | null {
  if (!content.startsWith("> ")) return null;
  const split = content.indexOf("\n\n");
  if (split === -1) return null;
  const body = content.slice(split + 2).trim();
  if (!body) return null;
  return { quote: content.slice(2, split).trim(), body };
}

function excerptOf(content: string): string {
  return content.replace(/\s+/g, " ").trim().slice(0, 140);
}

export function ChatInterface() {
  const [message, setMessage] = useState("");
  const [replyTo, setReplyTo] = useState<{
    id: string;
    role: "user" | "assistant";
    excerpt: string;
  } | null>(null);
  const {
    messages,
    isSending,
    isLive,
    queue,
    removeQueued,
    activeTool,
    chatError,
    send,
    stop,
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

  function submitCurrent() {
    // A new question is an explicit wish to see the answer, so re-attach.
    stickRef.current = true;
    const text = message.trim();
    if (!text) return;
    // The quote rides inside the sent text (see splitQuote), so a reply
    // works whether it goes out now or waits in the queue.
    const quoted = replyTo ? `> ${replyTo.excerpt}\n\n${text}` : text;
    if (send(quoted)) {
      setMessage("");
      setReplyTo(null);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submitCurrent();
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends (or queues while a turn runs); Shift+Enter is a newline,
    // left to the textarea's own behaviour.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitCurrent();
    }
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
        <span className="ml-auto inline-flex shrink-0 items-center gap-1.5">
          {(isSending || isLive) && (
            <button
              type="button"
              onClick={stop}
              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-red-400/40 px-2 py-1 text-[11px] text-red-300 transition-colors hover:border-red-400/70 hover:text-red-200"
              title="Stop this request, on the backend as well"
            >
              <Square size={11} aria-hidden="true" />
              Stop
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setReplyTo(null);
              newChat();
            }}
            disabled={isSending}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-50"
            title="Start a new conversation - this one stays stored under Chats"
          >
            <Plus size={12} aria-hidden="true" />
            New chat
          </button>
        </span>
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
                  onClick={() => {
                    stickRef.current = true;
                    setReplyTo(null);
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

        {messages.map((entry) => {
          if (entry.role === "user") {
            const quoted = splitQuote(entry.content);
            return (
              <div key={entry.id} className="chat-row chat-row--user">
                <div className="chat-bubble chat-bubble--user">
                  {quoted && <span className="chat-quote">{quoted.quote}</span>}
                  {quoted ? quoted.body : entry.content}
                </div>
                <button
                  type="button"
                  className="chat-reply-btn"
                  onClick={() =>
                    setReplyTo({
                      id: entry.id,
                      role: "user",
                      excerpt: excerptOf(quoted ? quoted.body : entry.content),
                    })
                  }
                  aria-label="Reply to this message"
                >
                  <Reply size={11} aria-hidden="true" />
                  Reply
                </button>
              </div>
            );
          }
          return (
            <div key={entry.id} className="chat-row chat-row--assistant">
              <span className="chat-avatar" aria-hidden="true">
                {/* The clip loops only while this message is the live one:
                a finished answer keeps a still frame instead of looping
                forever next to a completed reply. The key remounts the
                video when liveness flips, because flipping autoPlay alone
                never pauses a video that is already playing. */}
                <video
                  key={isLive && entry.id === lastMessageId ? "live" : "still"}
                  className="chat-avatar-clip"
                  src="/chat.mp4"
                  aria-hidden="true"
                  muted
                  loop={isLive && entry.id === lastMessageId}
                  playsInline
                  autoPlay={isLive && entry.id === lastMessageId}
                  preload="metadata"
                  tabIndex={-1}
                />
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
              {/* A live typing row has no text yet: quoting an empty
                  answer would produce a reply bar with an empty excerpt. */}
              {entry.content.trim().length > 0 && (
                <button
                  type="button"
                  className="chat-reply-btn"
                  onClick={() =>
                    setReplyTo({
                      id: entry.id,
                      role: "assistant",
                      excerpt: excerptOf(entry.content),
                    })
                  }
                  aria-label="Reply to this answer"
                >
                  <Reply size={11} aria-hidden="true" />
                  Reply
                </button>
              )}
            </div>
          );
        })}
      </div>

      <div className="chat-foot shrink-0">
        {replyTo && (
          <div className="chat-reply-bar">
            <span className="chat-reply-bar-text">
              Replying to {replyTo.role === "assistant" ? "this answer" : "your message"}: “{replyTo.excerpt}”
            </span>
            <button
              type="button"
              onClick={() => setReplyTo(null)}
              aria-label="Cancel reply"
              className="shrink-0 text-muted hover:text-foreground"
            >
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        )}
        {queue.length > 0 && (
          <div className="chat-queue" role="status" aria-label="Queued messages">
            {queue.map((queued, index) => (
              <span key={`${index}-${queued}`} className="chat-queue-chip">
                <span className="chat-queue-chip-text">
                  Queued: {queued.length > 60 ? `${queued.slice(0, 60)}…` : queued}
                </span>
                <button
                  type="button"
                  onClick={() => removeQueued(index)}
                  aria-label="Remove queued message"
                >
                  <X size={11} aria-hidden="true" />
                </button>
              </span>
            ))}
          </div>
        )}
        <form
          onSubmit={handleSubmit}
          className="flex items-center gap-2 rounded-xl border border-border bg-surface-raised/60 px-2.5 py-1.5 focus-within:border-cyan-300/40"
        >
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={handleComposerKey}
            placeholder={
              isSending
                ? "Type a follow-up - it will queue"
                : "Ask JARVIS about the business..."
            }
            aria-label="Message Biztek Assistant"
            rows={1}
            className="chat-composer-input min-w-0 flex-1 bg-transparent px-1 text-[0.95rem] text-foreground outline-none placeholder:text-muted"
          />
          <button
            type="submit"
            aria-label="Send message"
            title={isSending ? "Queue message" : "Send message"}
            disabled={message.trim().length === 0}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-cyan-300 to-violet-400 text-[#081016] disabled:cursor-not-allowed disabled:opacity-40"
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
