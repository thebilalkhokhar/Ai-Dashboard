"use client";

import { AlertTriangle } from "lucide-react";
import type { UnpaidInvoice } from "@/types/api";
import { formatMoney } from "@/lib/format";
import { CustomerCell, ShowMore, TableShell } from "@/components/dashboard/table-shell";
import { EmptyNote } from "@/components/dashboard/primitives";

export function UnpaidTable({
  rows,
  totalCount,
  rowLimit,
  onShowMore,
  onSelectCustomer,
}: {
  rows: UnpaidInvoice[];
  totalCount: number;
  rowLimit: number;
  onShowMore: () => void;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const visible = rows.slice(0, rowLimit);
  return (
    <TableShell
      title="Unpaid invoices"
      icon={AlertTriangle}
      count={visible.length}
      totalCount={totalCount}
    >
      {rows.length === 0 ? (
        <EmptyNote>No invoices currently carry a balance.</EmptyNote>
      ) : (
        <>
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Invoice</th>
                <th className="px-4 py-3 font-medium">Customer</th>
                <th className="px-4 py-3 font-medium">Payment status</th>
                <th className="px-4 py-3 text-right font-medium">Balance</th>
                <th className="px-4 py-3 text-right font-medium">Total</th>
                <th className="px-4 py-3 text-right font-medium">Age</th>
                <th className="px-4 py-3 font-medium">Overdue</th>
                <th className="px-4 py-3 font-medium">Rep</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visible.map((invoice, index) => (
                <tr key={invoice.id ?? `${invoice.inv_ref ?? "invoice"}-${index}`}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{invoice.inv_ref || "—"}</p>
                    <p className="mt-1 text-xs text-muted">ID {invoice.id ?? "—"}</p>
                  </td>
                  <td className="px-4 py-3">
                    <CustomerCell customer={invoice.customer} onSelectCustomer={onSelectCustomer} />
                  </td>
                  <td className="px-4 py-3 text-muted">
                    {invoice.payment_status_label || "Status not provided"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatMoney(invoice.balance)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {formatMoney(invoice.total_amount)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {invoice.days_invoice_age}d
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-md px-2 py-1 text-[11px] ${
                        invoice.overdue
                          ? "bg-red-400/10 text-red-300"
                          : "bg-surface-raised text-muted"
                      }`}
                    >
                      {invoice.overdue ? "Overdue" : "Within terms"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted">{invoice.sale_rep?.name || "Unassigned"}</td>
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
