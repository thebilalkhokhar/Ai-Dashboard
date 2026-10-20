import { ArrowUpRight, Check, Clock3, X } from "lucide-react";
import { approvalInboxData, approvalRequests } from "@/lib/mockData";

export function ApprovalInbox() {
  return (
    <section aria-labelledby="approvals-title" className="rounded-xl border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-5 py-4">
        <div>
          <h2 id="approvals-title" className="font-semibold">Approval inbox</h2>
          <p className="mt-1 text-xs text-muted">Human review required before execution</p>
        </div>
        <span className="rounded-full bg-surface-raised px-2.5 py-1 text-xs text-accent">
          {approvalInboxData.pendingCount} pending
        </span>
      </div>
      <div className="divide-y divide-border">
        {approvalRequests.map((request) => (
          <article key={request.id} className="p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-medium">{request.title}</h3>
                  <span className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted">
                    {request.category}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted">{request.description}</p>
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                  <span>{request.id}</span>
                  <span>Requested by {request.requestedBy}</span>
                  <span className="inline-flex items-center gap-1">
                    <Clock3 size={12} aria-hidden="true" />
                    {request.submittedAt}
                  </span>
                </div>
              </div>
              <p className="text-lg font-semibold tabular-nums">{request.amount}</p>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
              >
                <X size={15} aria-hidden="true" />
                Reject
              </button>
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-[#11150b] transition-opacity hover:opacity-90"
              >
                <Check size={15} aria-hidden="true" />
                Approve
                <ArrowUpRight size={13} aria-hidden="true" />
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
