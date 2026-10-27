"use client";

// The dashboard shell: live status header, the five tabs and the wiring between
// them. The data lives in hooks (use-live-data, use-signals, use-stuck-leads)
// and each tab renders a panel from components/dashboard/*.

import { useState, type ReactNode } from "react";
import { useLiveData } from "@/hooks/use-live-data";
import { ROW_PAGE_SIZE, useSignals } from "@/hooks/use-signals";
import { useStuckLeads } from "@/hooks/use-stuck-leads";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { OverviewPanel } from "@/components/dashboard/overview-panel";
import { LeadsPanel, PipelinePanel, ReceivablesPanel } from "@/components/dashboard/panels";
import { CommandCenter } from "@/components/command-center/command-center";
import { ChatHistoryPanel } from "@/components/chat-history";
import { formatDateTime } from "@/lib/format";

export function Dashboard({
  actionCenter,
  onSelectCustomer,
  onTabChange,
}: {
  actionCenter: ReactNode;
  onSelectCustomer: (customerId: number | null) => void;
  onTabChange?: (tab: string) => void;
}) {
  const { snapshot, error: liveError, refresh } = useLiveData();
  const [activeTab, setActiveTab] = useState("command-center");

  const signals = useSignals({
    activeTab,
    snapshotAt: snapshot?.generated_at ?? null,
  });
  const leads = useStuckLeads({
    activeTab,
    reloadKey: signals.reloadKey,
    reload: signals.retry,
  });

  function selectTab(tab: string) {
    setActiveTab(tab);
    onTabChange?.(tab);
    signals.onTabChange(tab);
    if (tab === "leads") leads.setLeadRowLimit(() => ROW_PAGE_SIZE);
  }

  return (
    <section aria-labelledby="dashboard-title" className="space-y-6">
      {/* Persistent Context: Metrics and Signals timestamps prominently visible & labeled */}
      <div className="flex flex-col sm:flex-row sm:items-baseline sm:justify-between gap-3 border-b border-border/60 pb-4">
        <div>
          <h1 id="dashboard-title" className="text-2xl sm:text-3xl font-semibold tracking-tight text-foreground font-sans">
            Command center
          </h1>
          <p className="mt-1 text-xs text-muted">
            Real-time operations, ERP/Zoho mirrors, and autonomous assistant dispatch
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-mono shadow-sm">
          <div className="inline-flex items-center gap-1.5">
            <span className="text-muted">Metrics as of</span>
            <span className="font-semibold text-foreground">
              {snapshot ? formatDateTime(snapshot.generated_at) : "—"}
            </span>
          </div>
          <span className="text-border" aria-hidden="true">|</span>
          <div className="inline-flex items-center gap-1.5">
            <span className="text-muted">Signals as of</span>
            <span className="font-semibold text-foreground">
              {signals.signals ? formatDateTime(signals.signals.generated_at) : "—"}
            </span>
          </div>
        </div>
      </div>

      {(liveError || signals.signalsError) && (
        <div
          className="rounded-xl border border-negative-border bg-negative-muted px-4 py-3 text-sm text-negative-foreground shadow-sm"
          role="alert"
        >
          {liveError && <p>{liveError}</p>}
          {signals.signalsError && (
            <p className={liveError ? "mt-1" : ""}>{signals.signalsError}</p>
          )}
        </div>
      )}

      <Tabs value={activeTab} onValueChange={(value) => selectTab(String(value))}>
        <TabsList aria-label="Dashboard sections" className="max-w-full flex-wrap">
          <TabsTrigger value="command-center">Command Center</TabsTrigger>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="receivables">Receivables</TabsTrigger>
          <TabsTrigger value="pipeline">Pipeline</TabsTrigger>
          <TabsTrigger value="leads">Leads</TabsTrigger>
          <TabsTrigger value="ai-review">AI review</TabsTrigger>
          <TabsTrigger value="chats">Chats</TabsTrigger>
        </TabsList>

        <TabsContent value="command-center">
          <CommandCenter />
        </TabsContent>

        <TabsContent value="overview" className="space-y-5">
          <OverviewPanel
            snapshot={snapshot}
            signals={signals.signals}
            stuckLeads={leads.stuckLeads}
            onSelectTab={selectTab}
            onRefresh={() => void refresh()}
          />
        </TabsContent>

        <TabsContent value="receivables">
          <ReceivablesPanel signals={signals} onSelectCustomer={onSelectCustomer} />
        </TabsContent>

        <TabsContent value="pipeline">
          <PipelinePanel signals={signals} onSelectCustomer={onSelectCustomer} />
        </TabsContent>

        <TabsContent value="leads" className="space-y-4">
          <LeadsPanel leads={leads} />
        </TabsContent>

        <TabsContent value="ai-review">{actionCenter}</TabsContent>
        <TabsContent value="chats">
          <ChatHistoryPanel onOpenThread={() => selectTab("command-center")} />
        </TabsContent>
      </Tabs>

      <p className="text-[11px] font-mono text-muted pt-6 border-t border-border/40">
        All totals are computed by the backend. This UI only formats and renders them.
      </p>
    </section>
  );
}
