"use client";

// The dashboard shell: live status header, the five tabs and the wiring between
// them. The data lives in hooks (use-live-data, use-signals, use-stuck-leads)
// and each tab renders a panel from components/dashboard/*.

import { RefreshCw } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useLiveData } from "@/hooks/use-live-data";
import { ROW_PAGE_SIZE, useSignals } from "@/hooks/use-signals";
import { useStuckLeads } from "@/hooks/use-stuck-leads";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { OverviewPanel } from "@/components/dashboard/overview-panel";
import { LeadsPanel, PipelinePanel, ReceivablesPanel } from "@/components/dashboard/panels";
import { CommandCenter } from "@/components/command-center/command-center";
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
  const { snapshot, status, error: liveError, lastEventAt, refresh } = useLiveData();
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

  const liveLabel =
    status === "live"
      ? "Live stream connected"
      : status === "connecting"
        ? "Connecting to live stream..."
        : status === "reconnecting"
          ? "Live stream reconnecting..."
          : "Live stream offline";
  const liveDot =
    status === "live"
      ? "bg-emerald-400"
      : status === "offline"
        ? "bg-red-400"
        : "bg-amber-300";

  return (
    <section aria-labelledby="dashboard-title" className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="space-y-0.5 text-xs text-muted">
            <p>Metrics as of {snapshot ? formatDateTime(snapshot.generated_at) : "—"}</p>
            <p>
              Signals as of {signals.signals ? formatDateTime(signals.signals.generated_at) : "—"}
            </p>
          </div>
          <h1 id="dashboard-title" className="mt-1 text-2xl font-semibold tracking-tight">
            Command center
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-muted"
            role="status"
            title={liveError || liveLabel}
          >
            <span className={`h-2 w-2 rounded-full ${liveDot}`} aria-hidden="true" />
            {liveLabel}
          </span>
          {lastEventAt && (
            <span className="text-xs text-muted">Updated {formatDateTime(lastEventAt)}</span>
          )}
          <button
            type="button"
            onClick={() => {
              void refresh();
              signals.retry();
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
          >
            <RefreshCw size={13} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </div>

      {(liveError || signals.signalsError) && (
        <div
          className="rounded-xl border border-red-400/30 bg-red-400/5 px-4 py-3 text-sm text-red-300"
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
      </Tabs>

      <p className="text-[11px] text-muted">
        All totals are computed by the backend. This UI only formats and renders them.
      </p>
    </section>
  );
}
