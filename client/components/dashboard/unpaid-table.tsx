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
          <div className="overflow-x-auto">
            <table className="w-full min-w-[840px] text-left text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-raised/40 text-[11px] font-semibold uppercase tracking-wider text-muted">
                  <th scope="col" className="px-4 py-3">Invoice</th>
                  <th scope="col" className="px-4 py-3">Customer</th>
                  <th scope="col" className="px-4 py-3">Payment status</th>
                  <th scope="col" className="px-4 py-3 text-right">Balance</th>
                  <th scope="col" className="px-4 py-3 text-right">Total</th>
                  <th scope="col" className="px-4 py-3 text-right">Age</th>
                  <th scope="col" className="px-4 py-3">Overdue</th>
                  <th scope="col" className="px-4 py-3">Rep</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {visible.map((invoice, index) => {
                  const isOverdue = Boolean(invoice.overdue);
                  return (
                    <tr
                      key={invoice.id ?? `${invoice.inv_ref ?? "invoice"}-${index}`}
                      className="group transition-colors hover:bg-surface-raised/50"
                    >
                      <td className="px-4 py-3.5 align-middle">
                        <p className="font-mono text-sm font-semibold text-foreground group-hover:text-accent transition-colors">
                          {invoice.inv_ref || "—"}
                        </p>
                        <p className="mt-0.5 font-mono text-[11px] text-muted">ID {invoice.id ?? "—"}</p>
                      </td>
                      <td className="px-4 py-3.5 align-middle">
                        <CustomerCell customer={invoice.customer} onSelectCustomer={onSelectCustomer} />
                      </td>
                      <td className="px-4 py-3.5 align-middle text-xs text-muted">
                        <span className="inline-flex items-center rounded-md bg-surface-raised px-2 py-0.5 text-xs text-foreground/80 border border-border/60">
                          {invoice.payment_status_label || "Status not provided"}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 text-right align-middle">
                        <span className={`font-mono text-sm font-bold tracking-tight tabular-nums ${
                          isOverdue ? "text-negative" : "text-foreground"
                        }`}>
                          {formatMoney(invoice.balance)}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 text-right align-middle font-mono text-xs tabular-nums text-muted">
                        {formatMoney(invoice.total_amount)}
                      </td>
                      <td className="px-4 py-3.5 text-right align-middle">
                        <span className={`font-mono text-xs tabular-nums ${
                          invoice.days_invoice_age > 60
                            ? "font-semibold text-warning"
                            : "text-muted"
                        }`}>
                          {invoice.days_invoice_age}d
                        </span>
                      </td>
                      <td className="px-4 py-3.5 align-middle">
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wider ${
                            isOverdue
                              ? "bg-negative-muted text-negative border border-negative-border"
                              : "bg-surface-raised text-muted border border-border"
                          }`}
                        >
                          {isOverdue ? "Overdue" : "Within terms"}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 align-middle text-xs text-foreground/80">
                        {invoice.sale_rep?.name || <span className="text-muted italic">Unassigned</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <ShowMore shown={visible.length} total={totalCount} onShowMore={onShowMore} />
        </>
      )}
    </TableShell>
  );
}
