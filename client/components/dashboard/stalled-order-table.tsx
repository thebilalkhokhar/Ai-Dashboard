"use client";

import { Boxes } from "lucide-react";
import type { StalledOrder } from "@/types/api";
import { formatDate, formatMoney } from "@/lib/format";
import { CustomerCell, ShowMore, TableShell } from "@/components/dashboard/table-shell";
import { EmptyNote } from "@/components/dashboard/primitives";

export function StalledOrderTable({
  rows,
  totalCount,
  reliable,
  rowLimit,
  onShowMore,
  onSelectCustomer,
}: {
  rows: StalledOrder[];
  totalCount: number;
  reliable: boolean;
  rowLimit: number;
  onShowMore: () => void;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const visible = rows.slice(0, rowLimit);
  return (
    <TableShell
      title="Stalled orders"
      icon={Boxes}
      count={visible.length}
      totalCount={totalCount}
      warning={
        !reliable
          ? "The backend marks this signal unreliable: the ERP exposes no order status timestamps, so these rows are derived from invoice age only. Do not use them for decisions."
          : undefined
      }
    >
      {rows.length === 0 ? (
        <EmptyNote>No orders match the current stalled threshold.</EmptyNote>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[800px] text-left text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-raised/40 text-[11px] font-semibold uppercase tracking-wider text-muted">
                  <th scope="col" className="px-4 py-3">Order</th>
                  <th scope="col" className="px-4 py-3">Customer</th>
                  <th scope="col" className="px-4 py-3">Status</th>
                  <th scope="col" className="px-4 py-3 text-right">Amount</th>
                  <th scope="col" className="px-4 py-3 text-right">Invoice age</th>
                  <th scope="col" className="px-4 py-3">Invoice date</th>
                  <th scope="col" className="px-4 py-3">Rep</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {visible.map((order, index) => (
                  <tr
                    key={order.id ?? `${order.inv_ref ?? "order"}-${index}`}
                    className="group transition-colors hover:bg-surface-raised/50"
                  >
                    <td className="px-4 py-3.5 align-middle">
                      <p className="font-mono text-sm font-semibold text-foreground group-hover:text-accent transition-colors">
                        {order.inv_ref || "—"}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] text-muted">ID {order.id ?? "—"}</p>
                    </td>
                    <td className="px-4 py-3.5 align-middle">
                      <CustomerCell customer={order.customer} onSelectCustomer={onSelectCustomer} />
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs">
                      <span className="inline-flex items-center rounded-md bg-surface-raised px-2 py-0.5 text-xs text-foreground/80 border border-border/60">
                        {order.status_label || "Unknown"} ·{" "}
                        {order.payment_status_label || "Payment unknown"}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right align-middle">
                      <span className="font-mono text-sm font-semibold tracking-tight tabular-nums text-foreground">
                        {formatMoney(order.total_amount)}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right align-middle">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full font-mono text-xs font-semibold tabular-nums bg-warning-muted text-warning-foreground border border-warning-border">
                        {order.days_invoice_age}d
                      </span>
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs text-muted">
                      {formatDate(order.invoice_date)}
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs text-foreground/80">
                      {order.sale_rep?.name || <span className="text-muted italic">Unassigned</span>}
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
