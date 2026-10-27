"use client";

import { UserX } from "lucide-react";
import type { StuckLead } from "@/types/api";
import { ShowMore, TableShell } from "@/components/dashboard/table-shell";
import { EmptyNote } from "@/components/dashboard/primitives";

function artworkLabel(present: boolean | null): string {
  if (present === true) return "Provided";
  if (present === false) return "Missing";
  return "Not stated";
}

export function StuckLeadsTable({
  rows,
  totalCount,
  rowLimit,
  onShowMore,
}: {
  rows: StuckLead[];
  totalCount: number;
  rowLimit: number;
  onShowMore: () => void;
}) {
  const visible = rows.slice(0, rowLimit);
  return (
    <TableShell title="Stuck leads" icon={UserX} count={visible.length} totalCount={totalCount}>
      {rows.length === 0 ? (
        <EmptyNote>No leads match the stuck-lead filter right now.</EmptyNote>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[880px] text-left text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-raised/40 text-[11px] font-semibold uppercase tracking-wider text-muted">
                  <th scope="col" className="px-4 py-3">Lead</th>
                  <th scope="col" className="px-4 py-3">Company</th>
                  <th scope="col" className="px-4 py-3">Status</th>
                  <th scope="col" className="px-4 py-3 text-right">Last activity</th>
                  <th scope="col" className="px-4 py-3">Product</th>
                  <th scope="col" className="px-4 py-3">Missing</th>
                  <th scope="col" className="px-4 py-3">Artwork</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {visible.map((lead) => (
                  <tr
                    key={lead.lead_id}
                    className="group transition-colors hover:bg-surface-raised/50"
                  >
                    <td className="px-4 py-3.5 align-middle">
                      <p className="font-semibold text-foreground group-hover:text-accent transition-colors">
                        {lead.contact_name || "Name not provided"}
                      </p>
                      <p className="mt-0.5 text-xs text-muted">{lead.source || "Unknown source"}</p>
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs text-foreground/90">
                      {lead.company || "—"}
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs">
                      <span className="inline-flex items-center rounded-md bg-surface-raised px-2 py-0.5 text-xs text-foreground/80 border border-border/60">
                        {lead.status}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right align-middle">
                      <span className="font-mono text-xs tabular-nums text-muted">
                        {lead.days_since_activity}d
                      </span>
                    </td>
                    <td className="px-4 py-3.5 align-middle text-xs font-medium text-foreground/90">
                      {lead.product || lead.type || "—"}
                    </td>
                    <td className="px-4 py-3.5 align-middle">
                      {lead.missing.length === 0 ? (
                        <span className="text-xs text-muted italic">Nothing stated missing</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {lead.missing.map((field) => (
                            <span
                              key={field}
                              className="inline-flex items-center rounded-md bg-warning-muted border border-warning-border px-2 py-0.5 font-mono text-[11px] font-medium text-warning-foreground"
                            >
                              {field.replaceAll("_", " ")}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3.5 align-middle">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wider ${
                          lead.artwork_present === true
                            ? "bg-positive-muted text-positive border border-positive-border"
                            : lead.artwork_present === false
                              ? "bg-warning-muted text-warning-foreground border border-warning-border"
                              : "bg-surface-raised text-muted border border-border"
                        }`}
                      >
                        {artworkLabel(lead.artwork_present)}
                      </span>
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
