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
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Lead</th>
                <th className="px-4 py-3 font-medium">Company</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Last activity</th>
                <th className="px-4 py-3 font-medium">Product</th>
                <th className="px-4 py-3 font-medium">Missing</th>
                <th className="px-4 py-3 font-medium">Artwork</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visible.map((lead) => (
                <tr key={lead.lead_id}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{lead.contact_name || "Name not provided"}</p>
                    <p className="mt-1 text-xs text-muted">{lead.source || "Unknown source"}</p>
                  </td>
                  <td className="px-4 py-3 text-muted">{lead.company || "—"}</td>
                  <td className="px-4 py-3 text-muted">{lead.status}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {lead.days_since_activity}d
                  </td>
                  <td className="px-4 py-3">{lead.product || lead.type || "—"}</td>
                  <td className="px-4 py-3">
                    {lead.missing.length === 0 ? (
                      <span className="text-xs text-muted">Nothing stated missing</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {lead.missing.map((field) => (
                          <span
                            key={field}
                            className="rounded-md bg-amber-400/10 px-2 py-0.5 text-[11px] text-amber-300"
                          >
                            {field.replaceAll("_", " ")}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-md px-2 py-1 text-[11px] ${
                        lead.artwork_present === true
                          ? "bg-emerald-400/10 text-emerald-300"
                          : lead.artwork_present === false
                            ? "bg-amber-400/10 text-amber-300"
                            : "bg-surface-raised text-muted"
                      }`}
                    >
                      {artworkLabel(lead.artwork_present)}
                    </span>
                  </td>
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
