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
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Order</th>
                <th className="px-4 py-3 font-medium">Customer</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Amount</th>
                <th className="px-4 py-3 text-right font-medium">Invoice age</th>
                <th className="px-4 py-3 font-medium">Invoice date</th>
                <th className="px-4 py-3 font-medium">Rep</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visible.map((order, index) => (
                <tr key={order.id ?? `${order.inv_ref ?? "order"}-${index}`}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{order.inv_ref || "—"}</p>
                    <p className="mt-1 text-xs text-muted">ID {order.id ?? "—"}</p>
                  </td>
                  <td className="px-4 py-3">
                    <CustomerCell customer={order.customer} onSelectCustomer={onSelectCustomer} />
                  </td>
                  <td className="px-4 py-3 text-muted">
                    {order.status_label || "Unknown"} ·{" "}
                    {order.payment_status_label || "Payment unknown"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatMoney(order.total_amount)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {order.days_invoice_age}d
                  </td>
                  <td className="px-4 py-3 text-muted">{formatDate(order.invoice_date)}</td>
                  <td className="px-4 py-3 text-muted">{order.sale_rep?.name || "Unassigned"}</td>
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
