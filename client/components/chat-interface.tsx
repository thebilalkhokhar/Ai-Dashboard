import { Bot, Send, Sparkles } from "lucide-react";
import { chatData } from "@/lib/mockData";

export function ChatInterface() {
  return (
    <section aria-labelledby="chat-title" className="flex min-h-[32rem] flex-col rounded-xl border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-5 py-4">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-surface-raised text-accent">
            <Bot size={18} aria-hidden="true" />
          </span>
          <div>
            <h2 id="chat-title" className="text-sm font-semibold">{chatData.assistantName}</h2>
            <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              {chatData.status}
            </p>
          </div>
        </div>
        <Sparkles size={17} className="text-muted" aria-hidden="true" />
      </div>

      <div className="flex-1 space-y-5 p-5">
        <div className="ml-auto max-w-[85%] rounded-xl rounded-br-sm bg-surface-raised px-4 py-3 text-sm">
          Can you check the latest status of order ORD-77104?
        </div>
        <div className="flex max-w-[95%] gap-3">
          <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-surface-raised text-accent">
            <Bot size={15} aria-hidden="true" />
          </span>
          <div className="min-w-0 space-y-3">
            <div>
              <p className="text-sm font-medium">{chatData.markdownTitle}</p>
              <p className="mt-1 text-sm leading-6 text-muted">
                Order <code className="rounded bg-surface-raised px-1.5 py-0.5 font-mono text-xs text-foreground">{chatData.orderId}</code>{" "}
                {chatData.markdownBody}
              </p>
            </div>
            <div className="overflow-hidden rounded-lg border border-border">
              <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted">
                Structured response · JSON
              </div>
              <pre className="overflow-x-auto bg-[#0c0f15] p-3 text-xs leading-5 text-[#b7c4d8]">
                <code>{chatData.structuredJson}</code>
              </pre>
            </div>
          </div>
        </div>
      </div>

      <div className="border-t border-border p-4">
        <div className="flex items-center gap-3 rounded-lg border border-border bg-[#0c0f15] px-3 py-2">
          <span className="flex-1 px-1 text-sm text-muted">Message Biztek Assistant...</span>
          <button
            type="button"
            aria-label="Send message"
            className="grid h-8 w-8 place-items-center rounded-md bg-accent text-[#11150b]"
          >
            <Send size={15} aria-hidden="true" />
          </button>
        </div>
        <p className="mt-2 text-center text-[11px] text-muted">Presentation-only · responses use deterministic mock data</p>
      </div>
    </section>
  );
}
