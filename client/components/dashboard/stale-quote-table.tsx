"use client";

import { FileText } from "lucide-react";
import type { StaleQuote } from "@/types/api";
import { formatDate, formatMoney } from "@/lib/format";
import { CustomerCell, ShowMore, TableShell } from "@/components/dashboard/table-shell";
import { EmptyNote } from "@/components/dashboard/primitives";

export function StaleQuoteTable({
  rows,
  totalCount,
  rowLimit,
  onShowMore,
  onSelectCustomer,
}: {
  rows: StaleQuote[];
  totalCount: number;
  rowLimit: number;
  onShowMore: () => void;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const visible = rows.slice(0, rowLimit);
  return (
    <TableShell title="Stale quotes" icon={FileText} count={visible.length} totalCount={totalCount}>
      {rows.length === 0 ? (
        <EmptyNote>No open quotes match the current stale threshold.</EmptyNote>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[780px] text-left text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-raised/40 text-[11px] font-semibold uppercase tracking-wider text-muted">
                  <th scope="col" className="px-4 py-3">Quote</th>
                  <th scope="col" className="px-4 py-3">Customer</th>
                  <th scope="col" className="px-4 py-3">Status</th>
                  <th scope="col" className="px-4 py-3 text-right">Amount</th>
                  <th scope="col" className="px-4 py-3 text-right">Stale</th>
                  <th scope="col" className="px-4 py-3">Created</th>
                  <th scope="col" className="px-4 py-3">Rep</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {visible.map((quote, index) => (
                  <tr
                    key={quote.id ?? `${quote.ref ?? "quote"}-${index}`}
                    className="group transition-colors hover:bg-surface-raised/50"
                  >
                    <td className="px-4 py-3.5 align-middle">
                      <p className="font-mono text-sm font-semibold text-foreground group-hover:text-accent transition-colors">
                        {quote.ref || "—"}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] text-muted">ID {quote.id ?? "—"}</p>
                    </td>
                    <td className="px-4 py-3.5 align-middle">
                      <CustomerCell customer={quote.customer} onSelectCustomer={onSelectCustomer} />
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs">
                      <span className="inline-flex items-center rounded-md bg-surface-raised px-2 py-0.5 text-xs text-foreground/80 border border-border/60">
                        {quote.status || "Unknown"}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right align-middle">
                      <span className="font-mono text-sm font-semibold tracking-tight tabular-nums text-foreground">
                        {formatMoney(quote.amount)}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right align-middle">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full font-mono text-xs font-semibold tabular-nums bg-warning-muted text-warning-foreground border border-warning-border">
                        {quote.days_stale}d
                      </span>
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs text-muted">
                      {formatDate(quote.created_at)}
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs text-foreground/80">
                      {quote.sale_rep?.name || <span className="text-muted italic">Unassigned</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ShowMore shown={visible.length} total={totalCount} onShowMore={onShowMore} />
        </>
      )}
    </TableShell>
  );
}
