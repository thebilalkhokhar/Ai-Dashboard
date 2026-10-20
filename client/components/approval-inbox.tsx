"use client";

import { useState } from "react";
import { ArrowUpRight, Check, FileText, X } from "lucide-react";
import { quotes } from "@/lib/mockData";

export function ApprovalInbox() {
  const [pendingQuotes, setPendingQuotes] = useState([...quotes]);

  function handleDecision(id: number, decision: "Approved" | "Rejected") {
    setPendingQuotes((currentQuotes) => currentQuotes.filter((quote) => quote.id !== id));
    console.log(`Action ${decision}: ${id}`);
  }

  return (
    <section aria-labelledby="approvals-title" className="rounded-xl border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-5 py-4">
        <div>
          <h2 id="approvals-title" className="font-semibold">Quote review inbox</h2>
          <p className="mt-1 text-xs text-muted">Quote data from the mock API payload</p>
        </div>
        <span className="rounded-full bg-surface-raised px-2.5 py-1 text-xs text-accent">
          Pending review
        </span>
      </div>
      <div className="divide-y divide-border">
        {pendingQuotes.length === 0 ? (
          <p className="px-5 py-8 text-center text-sm text-muted">No quotes pending review.</p>
        ) : pendingQuotes.map((quote) => (
          <article key={quote.id} className="p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-medium">{quote.ref}</h3>
                  <span className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted">
                    {quote.status}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted">
                  {quote.customer.company_name} · {quote.sale_rep.name}
                </p>
                <p className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted">
                  <FileText size={13} aria-hidden="true" />
                  Quote ID {quote.id}
                </p>
              </div>
              <p className="text-lg font-semibold tabular-nums">{quote.amount}</p>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => handleDecision(quote.id, "Rejected")}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
              >
                <X size={15} aria-hidden="true" />
                Reject
              </button>
              <button
                type="button"
                onClick={() => handleDecision(quote.id, "Approved")}
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
