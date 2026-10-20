import { ApprovalInbox } from "@/components/approval-inbox";
import { ChatInterface } from "@/components/chat-interface";
import { Dashboard } from "@/components/dashboard";
import { CustomerContextPanel } from "@/components/customer-context";
import { SystemHealthBadge, SystemHealthProvider } from "@/components/system-health";
import { VoiceConsole } from "@/components/voice-console";

export default function Home() {
  return (
    <SystemHealthProvider>
      <main className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
        <header className="mb-8 flex items-center justify-between border-b border-border pb-5">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-accent font-bold text-[#11150b]">B</div>
            <div>
              <p className="text-sm font-semibold tracking-wide">BIZTEK <span className="text-accent">AI</span></p>
              <p className="text-[11px] text-muted">COMMAND CENTER</p>
            </div>
          </div>
          <div className="hidden sm:block">
            <SystemHealthBadge />
          </div>
        </header>

        <div className="space-y-8">
          <Dashboard />
          <CustomerContextPanel />
          <div className="grid items-start gap-5 xl:grid-cols-[1.3fr_0.7fr]">
            <ApprovalInbox />
            <VoiceConsole />
          </div>
          <div className="grid gap-5 lg:grid-cols-[1fr_0.8fr]">
            <ChatInterface />
            <div className="hidden rounded-xl border border-border bg-surface p-5 lg:block">
              <p className="text-sm font-semibold">Presentation layer</p>
              <p className="mt-2 text-sm leading-6 text-muted">
                Operational cards, approval affordances, voice controls, and chat responses are rendered from static,
                deterministic mock payloads. No actions connect to business systems.
              </p>
            </div>
          </div>
        </div>
      </main>
    </SystemHealthProvider>
  );
}
