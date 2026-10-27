"use client";

// The Receivables, Pipeline and Leads tabs: their filter bar, their tables and
// the loading / error states around them.

import { ErrorNote, FilterBar, LoadingNote } from "@/components/dashboard/primitives";
import { StaleQuoteTable } from "@/components/dashboard/stale-quote-table";
import { StalledOrderTable } from "@/components/dashboard/stalled-order-table";
import { StuckLeadsTable } from "@/components/dashboard/stuck-leads-table";
import { UnpaidTable } from "@/components/dashboard/unpaid-table";
import { ROW_PAGE_SIZE, type SignalsState } from "@/hooks/use-signals";
import type { StuckLeadsState } from "@/hooks/use-stuck-leads";

const STALE_DAY_OPTIONS = [3, 5, 7, 14, 30];
const STALLED_DAY_OPTIONS = [2, 3, 5, 7, 14];
const OVERDUE_DAY_OPTIONS = [7, 14, 30, 60, 90];

type SelectCustomer = (customerId: number | null) => void;

export function ReceivablesPanel({
  signals,
  onSelectCustomer,
}: {
  signals: SignalsState;
  onSelectCustomer: SelectCustomer;
}) {
  const showMore = () => signals.setRowLimit((current) => current + ROW_PAGE_SIZE);
  return (
    <div className="space-y-6">
      <FilterBar
        title="Receivables"
        description="Every invoice with a balance greater than zero. The overdue threshold changes which rows are flagged, not which rows are returned."
        filters={[
          {
            label: "Overdue after",
            value: signals.filters.overdueDays ?? 30,
            options: OVERDUE_DAY_OPTIONS,
            suffix: "days",
            onChange: (days) => signals.applyFilter({ overdueDays: days }),
          },
        ]}
      />
      {signals.isLoadingSignals && !signals.signals ? (
        <LoadingNote />
      ) : signals.signals ? (
        <UnpaidTable
          rows={signals.signals.unpaid_invoices}
          totalCount={signals.signals.summary.unpaid_invoices.count}
          rowLimit={signals.rowLimit}
          onShowMore={showMore}
          onSelectCustomer={onSelectCustomer}
        />
      ) : (
        <ErrorNote
          message={signals.signalsError || "Signals are unavailable."}
          onRetry={signals.retry}
        />
      )}
    </div>
  );
}

export function PipelinePanel({
  signals,
  onSelectCustomer,
}: {
  signals: SignalsState;
  onSelectCustomer: SelectCustomer;
}) {
  const showMore = () => signals.setRowLimit((current) => current + ROW_PAGE_SIZE);
  return (
    <div className="space-y-6">
      <FilterBar
        title="Pipeline"
        description="Open quotes and stalled orders. Both lists are computed by the backend; stalled orders are explicitly unreliable because the ERP has no status timestamps."
        filters={[
          {
            label: "Quotes stale after",
            value: signals.filters.staleDays ?? 3,
            options: STALE_DAY_OPTIONS,
            suffix: "days",
            onChange: (days) => signals.applyFilter({ staleDays: days }),
          },
          {
            label: "Orders stalled after",
            value: signals.filters.stalledDays ?? 2,
            options: STALLED_DAY_OPTIONS,
            suffix: "days",
            onChange: (days) => signals.applyFilter({ stalledDays: days }),
          },
        ]}
      />
      {signals.isLoadingSignals && !signals.signals ? (
        <LoadingNote />
      ) : signals.signals ? (
        <div className="space-y-6">
          <StaleQuoteTable
            rows={signals.signals.stale_quotes}
            totalCount={signals.signals.summary.stale_quotes.count}
            rowLimit={signals.rowLimit}
            onShowMore={showMore}
            onSelectCustomer={onSelectCustomer}
          />
          {signals.isLoadingStalledOrders && !signals.stalledOrders ? (
            <LoadingNote />
          ) : signals.stalledOrdersError ? (
            <ErrorNote message={signals.stalledOrdersError} onRetry={signals.retry} />
          ) : signals.stalledOrders ? (
            <StalledOrderTable
              rows={signals.stalledOrders}
              totalCount={signals.signals.summary.stalled_orders.count}
              reliable={signals.signals.stalled_orders.reliable}
              rowLimit={signals.rowLimit}
              onShowMore={showMore}
              onSelectCustomer={onSelectCustomer}
            />
          ) : null}
        </div>
      ) : (
        <ErrorNote
          message={signals.signalsError || "Signals are unavailable."}
          onRetry={signals.retry}
        />
      )}
    </div>
  );
}

export function LeadsPanel({ leads }: { leads: StuckLeadsState }) {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4 rounded-2xl border border-border/80 bg-surface/80 p-5 shadow-sm backdrop-blur-md">
        <div className="max-w-2xl">
          <h2 className="text-base font-semibold tracking-tight text-foreground">Leads</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Leads stuck before becoming an order: status New or Attempted to Contact, activity
            within the last {leads.stuckLeads?.days ?? 14} days, a description present, not flagged
            by the lead desk. The filter and every parsed field come from the backend
            (`GET /api/leads/stuck`) - this table only renders them.
          </p>
        </div>
      </div>
      {leads.isLoadingStuckLeads && !leads.stuckLeads ? (
        <LoadingNote />
      ) : leads.stuckLeads ? (
        <StuckLeadsTable
          rows={leads.stuckLeads.items}
          totalCount={leads.stuckLeads.total}
          rowLimit={leads.leadRowLimit}
          onShowMore={() => leads.setLeadRowLimit((current) => current + ROW_PAGE_SIZE)}
        />
      ) : (
        <ErrorNote
          message={leads.stuckLeadsError || "Stuck leads are unavailable."}
          onRetry={leads.retry}
        />
      )}
    </div>
  );
}
