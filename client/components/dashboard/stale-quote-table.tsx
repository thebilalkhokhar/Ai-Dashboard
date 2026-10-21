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
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Quote</th>
                <th className="px-4 py-3 font-medium">Customer</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Amount</th>
                <th className="px-4 py-3 text-right font-medium">Stale</th>
                <th className="px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3 font-medium">Rep</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visible.map((quote, index) => (
                <tr key={quote.id ?? `${quote.ref ?? "quote"}-${index}`}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{quote.ref || "—"}</p>
                    <p className="mt-1 text-xs text-muted">ID {quote.id ?? "—"}</p>
                  </td>
                  <td className="px-4 py-3">
                    <CustomerCell customer={quote.customer} onSelectCustomer={onSelectCustomer} />
                  </td>
                  <td className="px-4 py-3 text-muted">{quote.status || "Unknown"}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{formatMoney(quote.amount)}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {quote.days_stale}d
                  </td>
                  <td className="px-4 py-3 text-muted">{formatDate(quote.created_at)}</td>
                  <td className="px-4 py-3 text-muted">{quote.sale_rep?.name || "Unassigned"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ShowMore shown={visible.length} total={totalCount} onShowMore={onShowMore} />
        </>
      )}
    </TableShell>
  );
}
