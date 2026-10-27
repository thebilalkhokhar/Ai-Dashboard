"use client";

// The Overview tab: KPI tiles, the two charts and the "Needs attention" panel.
// Every number comes from the backend snapshot or the signals report; opening a
// tab from a card does exactly what clicking that tab would.

import {
  Activity,
  AlertTriangle,
  Boxes,
  Clock,
  DollarSign,
  FileText,
  RefreshCw,
  UserX,
  Users,
} from "lucide-react";
import { QuoteVolumeChart, RevenueTrendChart, TopRepsChart } from "@/components/charts";
import { AttentionRow, ChartCard, EmptyNote, KpiCard } from "@/components/dashboard/primitives";
import { SkeletonChartFrame, SkeletonKpiTile } from "@/components/ui/skeleton";
import { formatMoney } from "@/lib/format";
import type { DashboardSnapshot, SignalsReport, StuckLeadsResponse } from "@/types/api";

type Tab = "overview" | "receivables" | "pipeline" | "leads";

export function OverviewPanel({
  snapshot,
  signals,
  stuckLeads,
  onSelectTab,
  onRefresh,
}: {
  snapshot: DashboardSnapshot | null;
  signals: SignalsReport | null;
  stuckLeads: StuckLeadsResponse | null;
  onSelectTab: (tab: Tab) => void;
  onRefresh: () => void;
}) {
  if (!snapshot) {
    return (
      <div className="space-y-6">
        <section className="rounded-xl border border-border bg-surface p-8 text-center shadow-sm">
          <div className="mx-auto grid h-12 w-12 place-items-center rounded-xl border border-accent-border bg-accent-muted text-accent">
            <Activity size={24} aria-hidden="true" />
          </div>
          <p className="mt-3.5 text-base font-semibold text-foreground">Waiting for the first dashboard snapshot</p>
          <p className="mt-1 text-xs text-muted max-w-md mx-auto">
            The backend builds one on its scheduler cycle (`GET /api/metrics` returns 503 until then).
          </p>
          <button
            type="button"
            onClick={onRefresh}
            className="mt-5 inline-flex items-center gap-2 rounded-lg border border-border bg-surface-raised px-4 py-2 text-xs font-semibold text-foreground shadow-sm transition-all hover:border-accent/40 hover:bg-surface-overlay"
          >
            <RefreshCw size={13} aria-hidden="true" />
            Try again
          </button>
        </section>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 9 }).map((_, i) => (
            <SkeletonKpiTile key={i} />
          ))}
        </div>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]">
          <SkeletonChartFrame />
          <SkeletonChartFrame />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* 9 KPI Tiles in a balanced 3x3 dashboard grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <KpiCard
          icon={DollarSign}
          label="Revenue today"
          value={snapshot.revenue_today_display}
          detail={`${snapshot.payments_count_today} payments received`}
        />
        <KpiCard
          icon={Activity}
          label="Month to date"
          value={snapshot.revenue_month_to_date_display}
          detail="Payments received this month"
        />
        <KpiCard
          icon={Clock}
          label="Last 7 days"
          value={snapshot.revenue_last_7_days_display}
          detail="Today plus the previous 6 days"
        />
        <KpiCard
          icon={FileText}
          label="Open quotes"
          value={`${snapshot.open_quotes_count}`}
          detail={`${snapshot.open_quotes_value_display} in open value`}
        />
        <KpiCard
          icon={AlertTriangle}
          label="Unpaid balance"
          value={snapshot.unpaid_total_display}
          detail={`${snapshot.unpaid_count} invoices with a balance`}
        />
        <KpiCard
          icon={Boxes}
          label="Overdue balance"
          value={snapshot.overdue_total_display}
          detail={`${snapshot.overdue_count} invoices past the overdue threshold`}
        />
        <KpiCard
          icon={FileText}
          label="Stale quotes"
          value={`${signals?.summary.stale_quotes.count ?? "—"}`}
          detail={
            signals
              ? `${formatMoney(signals.summary.stale_quotes.total_amount)} in stalled value`
              : "Waiting for signals"
          }
        />
        <KpiCard
          icon={Users}
          label="Records scanned"
          value={`${snapshot.scanned.quotes.toLocaleString("en-US")} quotes`}
          detail={`${snapshot.scanned.orders.toLocaleString("en-US")} orders · ${snapshot.scanned.payments.toLocaleString("en-US")} payments`}
        />
        <KpiCard
          icon={UserX}
          label="Stuck leads"
          value={stuckLeads ? stuckLeads.total.toLocaleString("en-US") : "—"}
          detail={
            stuckLeads
              ? `Active in the last ${stuckLeads.days} days with a description · open the Leads tab`
              : "Waiting for stuck leads"
          }
          onActivate={() => onSelectTab("leads")}
        />
      </div>

      {/* 14-day Area & Bar Charts */}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]">
        <ChartCard title="Revenue received" subtitle="Last 14 days · from payments">
          <RevenueTrendChart data={snapshot.revenue_per_day} />
        </ChartCard>
        <ChartCard title="Quotes created" subtitle="Last 14 days · from quotes">
          <QuoteVolumeChart data={snapshot.quotes_per_day} />
        </ChartCard>
      </div>

      {/* Top Reps Ranking & Deterministic Needs Attention */}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <ChartCard title="Top reps this month" subtitle="Ranked by payments received">
          {snapshot.top_reps_this_month.length > 0 ? (
            <TopRepsChart reps={snapshot.top_reps_this_month} />
          ) : (
            <EmptyNote>No payments have been recorded this month.</EmptyNote>
          )}
        </ChartCard>

        <section className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-3 border-b border-border px-5 py-4 bg-surface-raised/40">
              <div className="grid h-8 w-8 place-items-center rounded-lg border border-warning-border bg-warning-muted text-warning">
                <AlertTriangle size={16} aria-hidden="true" />
              </div>
              <div>
                <h2 className="font-semibold text-foreground tracking-tight">Needs attention</h2>
                <p className="mt-0.5 text-xs text-muted">
                  Deterministic counts from `GET /api/signals`
                </p>
              </div>
            </div>
            <div className="space-y-3 p-4">
              <AttentionRow
                label="Stale quotes"
                count={signals?.summary.stale_quotes.count}
                amount={signals ? formatMoney(signals.summary.stale_quotes.total_amount) : undefined}
                onClick={() => onSelectTab("pipeline")}
              />
              <AttentionRow
                label="Unpaid invoices"
                count={signals?.summary.unpaid_invoices.count}
                amount={
                  signals ? formatMoney(signals.summary.unpaid_invoices.total_amount) : undefined
                }
                detail={
                  signals
                    ? `${signals.summary.unpaid_invoices.overdue_count} overdue · ${signals.summary.unpaid_invoices.partially_paid_count} partially paid`
                    : undefined
                }
                onClick={() => onSelectTab("receivables")}
              />
              <AttentionRow
                label="Stalled orders"
                count={signals?.summary.stalled_orders.count}
                amount={signals ? formatMoney(signals.summary.stalled_orders.total_amount) : undefined}
                detail="Signal is marked unreliable by the backend"
                onClick={() => onSelectTab("pipeline")}
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
