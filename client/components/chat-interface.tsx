"use client";

import { useState, type FormEvent } from "react";
import { Bot, Send } from "lucide-react";
import { chatData } from "@/lib/mockData";
import { sendMessage } from "@/services/api";

type ChatResponse = typeof chatData;
type ChatEntry =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; response: ChatResponse };

const initialMessages: ChatEntry[] = [
  { role: "user", content: "Can you check the latest status of order ORD-77104?" },
  {
    role: "assistant",
    content: chatData.markdownTitle,
    response: chatData,
  },
];

export function ChatInterface() {
  const [messages, setMessages] = useState<ChatEntry[]>(initialMessages);
  const [message, setMessage] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submittedMessage = message.trim();
    if (!submittedMessage || isSending) return;

    setMessage("");
    setErrorMessage("");
    setMessages((currentMessages) => [
      ...currentMessages,
      { role: "user", content: submittedMessage },
    ]);
    setIsSending(true);

    try {
      const response = await sendMessage(submittedMessage);
      setMessages((currentMessages) => [
        ...currentMessages,
        { role: "assistant", content: response.markdownTitle, response },
      ]);
    } catch (error) {
      console.error("Chat interaction failed.", error);
      setErrorMessage("Message could not be sent. Please try again.");
    } finally {
      setIsSending(false);
    }
  }

  return (
    <section aria-label={chatData.assistantName} className="flex h-full min-h-0 flex-col">
      <p className="flex shrink-0 items-center gap-2 px-5 py-3 text-xs text-muted">
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        {isSending ? "Thinking..." : chatData.status}
      </p>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-3" aria-live="polite">
        {messages.map((entry, index) =>
          entry.role === "user" ? (
            <div key={`user-${index}`} className="ml-auto max-w-[85%] rounded-xl rounded-br-sm bg-surface-raised px-4 py-3 text-sm">
              {entry.content}
            </div>
          ) : (
            <div key={`assistant-${index}`} className="flex max-w-[95%] gap-3">
              <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-surface-raised text-accent">
                <Bot size={15} aria-hidden="true" />
              </span>
              <div className="min-w-0 space-y-3">
                <div>
                  <p className="text-sm font-medium">{entry.content}</p>
                  {entry.response && (
                    <p className="mt-1 text-sm leading-6 text-muted">
                      Order{" "}
                      <code className="rounded bg-surface-raised px-1.5 py-0.5 font-mono text-xs text-foreground">
                        {entry.response.orderId}
                      </code>{" "}
                      {entry.response.markdownBody}
                    </p>
                  )}
                </div>
                {entry.response && (
                  <div className="overflow-hidden rounded-lg border border-border">
                    <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted">
                      Structured response · JSON
                    </div>
                    <pre className="overflow-x-auto bg-[#0c0f15] p-3 text-xs leading-5 text-[#b7c4d8]">
                      <code>{entry.response.structuredJson}</code>
                    </pre>
                  </div>
                )}
              </div>
            </div>
          ),
        )}
        {isSending && (
          <p className="ml-10 text-sm text-muted" role="status">Waiting for assistant response...</p>
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
        <p className="mt-2 text-center text-[11px] text-muted">Presentation-only · responses use deterministic mock data</p>
      </div>
    </section>
  );
}
