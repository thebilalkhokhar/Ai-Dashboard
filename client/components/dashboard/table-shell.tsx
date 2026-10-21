"use client";

// The frame every dashboard table shares: title bar with the row count, an
// optional reliability warning, the customer cell and the "Show more" control.

import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
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
    <section className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <Icon size={16} className="text-muted" aria-hidden="true" />
          <h2 className="text-sm font-semibold">{title}</h2>
        </div>
        <span className="text-xs text-muted">
          Showing {Math.min(count, totalCount).toLocaleString("en-US")} of{" "}
          {totalCount.toLocaleString("en-US")}
        </span>
      </div>
      {warning && (
        <p className="border-b border-amber-400/20 bg-amber-400/5 px-4 py-2 text-xs text-amber-300">
          {warning}
        </p>
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
    <div className="flex justify-center border-t border-border px-4 py-3">
      <button
        type="button"
        onClick={onShowMore}
        className="rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
      >
        Show {Math.min(ROW_PAGE_SIZE, total - shown)} more
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
    <div>
      <p>{customer?.company_name || "Unknown customer"}</p>
      {customerId ? (
        <button
          type="button"
          onClick={() => onSelectCustomer(customerId)}
          className="mt-0.5 text-xs text-accent transition-colors hover:text-accent/80"
        >
          View context
        </button>
      ) : (
        <p className="mt-0.5 text-xs text-muted">No customer ID</p>
      )}
    </div>
  );
}
