"use client";

// The frame every dashboard table shares: title bar with the row count, an
// optional reliability warning, the customer cell and the "Show more" control.

import type { ReactNode } from "react";
import { AlertTriangle, ChevronDown, type LucideIcon } from "lucide-react";
import type { CustomerRef } from "@/types/api";
import { ROW_PAGE_SIZE } from "@/hooks/use-signals";

export function TableShell({
  title,
  icon: Icon,
  count,
  totalCount,
  warning,
  children,
}: {
  title: string;
  icon: LucideIcon;
  count: number;
  totalCount: number;
  warning?: string;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-surface-raised/40 px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="grid h-7 w-7 place-items-center rounded-lg border border-border/70 bg-surface-raised text-muted">
            <Icon size={14} aria-hidden="true" />
          </div>
          <h2 className="text-sm font-semibold tracking-tight text-foreground">{title}</h2>
        </div>
        <span className="inline-flex items-center rounded-full border border-border bg-surface-raised px-3 py-1 text-xs font-mono font-medium text-muted">
          Showing {Math.min(count, totalCount).toLocaleString("en-US")} of{" "}
          {totalCount.toLocaleString("en-US")}
        </span>
      </div>
      {warning && (
        <div className="flex items-start gap-2.5 border-b border-warning-border bg-warning-muted px-5 py-2.5 text-xs text-warning-foreground">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
          <p className="leading-relaxed">{warning}</p>
        </div>
      )}
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}

export function ShowMore({
  shown,
  total,
  onShowMore,
}: {
  shown: number;
  total: number;
  onShowMore: () => void;
}) {
  if (shown >= total) return null;
  return (
    <div className="flex justify-center border-t border-border bg-surface-raised/20 px-4 py-3.5">
      <button
        type="button"
        onClick={onShowMore}
        className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-raised px-4 py-2 text-xs font-semibold text-foreground shadow-sm transition-all hover:border-accent/40 hover:bg-surface-overlay active:scale-95"
      >
        <span>Show {Math.min(ROW_PAGE_SIZE, total - shown)} more</span>
        <ChevronDown size={13} className="text-muted" aria-hidden="true" />
      </button>
    </div>
  );
}

function selectableCustomerId(customer: CustomerRef | null): number | null {
  const id = customer?.id ?? null;
  return id !== null && id > 0 ? id : null;
}

export function CustomerCell({
  customer,
  onSelectCustomer,
}: {
  customer: CustomerRef | null;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const customerId = selectableCustomerId(customer);
  return (
    <div className="space-y-0.5">
      <p className="font-medium text-foreground">{customer?.company_name || "Unknown customer"}</p>
      {customerId ? (
        <button
          type="button"
          onClick={() => onSelectCustomer(customerId)}
          className="inline-flex items-center gap-1 text-xs font-medium text-accent transition-colors hover:text-accent-hover hover:underline"
        >
          View context
        </button>
      ) : (
        <p className="text-xs text-muted">No customer ID</p>
      )}
    </div>
  );
}
