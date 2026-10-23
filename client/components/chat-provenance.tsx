"use client";

// Answer provenance.
//
// The chat footer says every answer is "verified against backend records". This
// makes that inspectable: the lookups this turn actually performed, with the
// duration and outcome the backend recorded for each. It is rendered only from
// the task's own event timeline - no event, no chip - and a turn whose stream
// never arrived says so plainly instead of showing an empty assurance.

import { useState } from "react";
import { Check, ShieldCheck, X } from "lucide-react";
import type { AnswerEvidence } from "@/lib/presentation";
import { formatMoney } from "@/lib/format";
import type { ChatToolResultEvent, CrmCustomerCard } from "@/types/api";

function milliseconds(value: number | null): string {
  if (value === null) return "still running";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(1)} s`;
}

const MONEY_LABELS: Record<string, string> = {
  sum_amount: "Total",
  sum_balance: "Total balance",
  total_balance: "Total balance",
  revenue_total: "Total revenue",
  total_payments: "Total received",
  total_amount: "Total",
  // the CRM ledger keeps its own label - the two are never merged
  sum_amount_received: "Total received · Zoho CRM",
};

const CARD_COUNT_LABELS: Record<string, string> = {
  projects: "Projects",
  estimates: "Estimates",
  invoices: "Invoices",
  payments: "Payments",
  timeline: "Interactions",
};

const CARD_TOTAL_LABELS: Record<string, string> = {
  estimates_total_amount: "Estimates",
  projects_total_amount: "Projects",
  invoices_total_amount: "Invoices",
  payments_total_amount: "Payments",
};

// The Customer 360 card: the CRM record an answer is about, projected by
// the backend from its own tool summary. Rendered only when a frame
// carries one - no frame, no card.
function CustomerCard({ card }: { card: CrmCustomerCard }) {
  const counts = Object.entries(card.counts ?? {});
  const totals = Object.entries(card.totals ?? {});
  const last = card.last_interaction;
  const lastDate = last?.at ? new Date(last.at) : null;
  const lastWhen =
    lastDate && !Number.isNaN(lastDate.getTime())
      ? lastDate.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
      : null;
  return (
    <section className="chat-360-card" data-testid="chat-360-card" aria-label="Customer 360 summary">
      <div className="chat-360-head">
        <p className="chat-360-name">{card.name ?? "CRM record"}</p>
        <div className="chat-360-badges">
          {card.record_type && <span className="chat-360-badge">{card.record_type}</span>}
          {card.status && <span className="chat-360-badge">{card.status}</span>}
          <span className="chat-360-badge">Zoho CRM</span>
        </div>
      </div>
      {card.company && <p className="chat-360-company">{card.company}</p>}
      {(card.email || card.phone) && (
        <p className="chat-360-contact">{[card.email, card.phone].filter(Boolean).join(" · ")}</p>
      )}
      {counts.length > 0 && (
        <p className="chat-360-counts">
          {counts.map(([key, value]) => `${CARD_COUNT_LABELS[key] ?? key} ${value}`).join(" · ")}
        </p>
      )}
      {totals.length > 0 && (
        <p className="chat-360-totals">
          {totals
            .map(([key, value]) => `${CARD_TOTAL_LABELS[key] ?? key} ${formatMoney(value)}`)
            .join(" · ")}
        </p>
      )}
      {last && (
        <p className="chat-360-last">
          Last interaction: {last.type ?? "activity"}
          {lastWhen ? ` · ${lastWhen}` : ""}
          {last.direction === "Inbound"
            ? " · customer wrote last"
            : last.direction === "Outbound"
              ? " · team wrote last"
              : ""}
        </p>
      )}
    </section>
  );
}

// The one thing a reader of a money answer asks next: does that total cover
// every match, or only the page on screen? Both halves come from the backend.
function moneyLine(frame: ChatToolResultEvent): string | null {
  if (typeof frame.money_total !== "number" || !Number.isFinite(frame.money_total)) {
    return null;
  }
  const label = MONEY_LABELS[frame.money_label ?? ""] ?? "Total";
  const amount = formatMoney(frame.money_total);
  if (frame.complete === false) {
    return `${label} ${amount} - covers ${frame.money_covers ?? "every matched record"}`;
  }
  if (frame.complete === true) {
    return `${label} ${amount} across all ${frame.records_total ?? 0} matched records`;
  }
  // an aggregate tool has no pages; its own count says what it totalled over
  if (typeof frame.record_count === "number") {
    return `${label} ${amount} across ${frame.record_count.toLocaleString("en-US")} records`;
  }
  return `${label} ${amount} - covers ${frame.money_covers ?? "every matched record"}`;
}

function windowLine(frame: ChatToolResultEvent): string | null {
  const from = frame.window?.date_from;
  const to = frame.window?.date_to;
  if (!from && !to) return null;
  if (from && to) return `${from} to ${to}`;
  return from ? `from ${from}` : `up to ${to}`;
}

// A plain-language line about how much a lookup established. Only built from
// the counts the backend stored; a partial page never reads as a full one.
function coverage(records: NonNullable<AnswerEvidence["records"]>): string {
  if (records.complete) {
    return `all ${records.total.toLocaleString("en-US")} matching records retrieved`;
  }
  const remaining =
    records.remaining === null
      ? "more still to fetch"
      : `${records.remaining.toLocaleString("en-US")} still to fetch`;
  return `${records.returned.toLocaleString("en-US")} of ${records.total.toLocaleString("en-US")} matched records, ${remaining}`;
}

export function AnswerProvenance({
  evidence,
  frames = [],
}: {
  evidence: AnswerEvidence[];
  frames?: ChatToolResultEvent[];
}) {
  const [open, setOpen] = useState<string | null>(null);

  if (evidence.length === 0) {
    return (
      <p className="chat-proof chat-proof-empty">
        No recorded lookup for this answer - the activity stream did not reach this browser.
      </p>
    );
  }

  const failed = evidence.filter((item) => !item.ok).length;
  // The last frame that carries anything the reader needs: a total, a window
  // or an incomplete page. Earlier pages are superseded by the walk.
  const summary = [...frames]
    .reverse()
    .find(
      (frame) =>
        typeof frame.money_total === "number" ||
        typeof frame.record_count === "number" ||
        frame.window !== undefined ||
        frame.complete === false,
    );
  // The Customer 360 card rides its own frame; the latest one wins.
  const cardFrame = [...frames].reverse().find((frame) => frame.customer_card);

  return (
    <div className="chat-proof" data-testid="chat-provenance">
      {cardFrame?.customer_card && <CustomerCard card={cardFrame.customer_card} />}
      {summary && (
        <dl className="chat-proof-facts" data-testid="chat-proof-facts">
          {(() => {
            const lines: { label: string; value: string }[] = [];
            const money = moneyLine(summary);
            if (money) lines.push({ label: "Verified total", value: money });
            const windowText = windowLine(summary);
            if (windowText) lines.push({ label: "Date range", value: windowText });
            if (typeof summary.records_total === "number") {
              lines.push({
                label: "Records",
                value:
                  summary.complete === true
                    ? `all ${summary.records_total.toLocaleString("en-US")} matched records retrieved`
                    : `${summary.records_returned?.toLocaleString("en-US") ?? 0} of ${summary.records_total.toLocaleString("en-US")} matched` +
                      (typeof summary.records_remaining === "number"
                        ? `, ${summary.records_remaining.toLocaleString("en-US")} still to fetch`
                        : ""),
              });
            } else if (typeof summary.record_count === "number") {
              // an aggregate the backend totalled itself - no pages to walk
              lines.push({
                label: "Records",
                value: `${summary.record_count.toLocaleString("en-US")} records totalled`,
              });
            }
            return lines.map((line) => (
              <div key={line.label} className="chat-proof-fact">
                <dt>{line.label}</dt>
                <dd>{line.value}</dd>
              </div>
            ));
          })()}
        </dl>
      )}
      <p className="chat-proof-head">
        <ShieldCheck size={12} aria-hidden="true" />
        Checked against {evidence.length} backend {evidence.length === 1 ? "lookup" : "lookups"}
        {failed > 0 && <span className="chat-proof-failed">{failed} did not complete</span>}
      </p>
      <ul className="chat-proof-list">
        {evidence.map((item) => (
          <li key={item.event_id}>
            <button
              type="button"
              className="chat-proof-chip"
              data-ok={item.ok ? "true" : "false"}
              aria-expanded={open === item.event_id}
              onClick={() => setOpen((value) => (value === item.event_id ? null : item.event_id))}
            >
              {item.ok ? <Check size={11} aria-hidden="true" /> : <X size={11} aria-hidden="true" />}
              {item.label}
              {/* a real separator, so the duration does not read as part of the
                  label to a screen reader or in copied text */}
              <span className="chat-proof-ms">· {milliseconds(item.durationMs)}</span>
            </button>
            {open === item.event_id && (
              <span className="chat-proof-detail">
                {item.records
                  ? `${coverage(item.records)} · `
                  : "No record counts were stored for this lookup · "}
                tool <code>{item.tool}</code> · recorded {new Date(item.at).toLocaleTimeString()} ·
                reported by the task event stream, not by the model
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
