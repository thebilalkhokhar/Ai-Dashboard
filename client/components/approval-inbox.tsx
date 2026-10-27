"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Building,
  Check,
  Clipboard,
  Clock,
  Coins,
  Play,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { fetchLatestBriefing, startAgentRun } from "@/services/api";
import { useLiveData } from "@/hooks/use-live-data";
import type {
  AgentRunBriefing,
  CollectionsOutput,
  CrmDealWatchOutput,
  CrmEngagementOutput,
  LeadFollowupOutput,
  NegotiationOutput,
} from "@/types/api";

const RUN_POLL_INTERVAL_MS = 4000;
const RUN_POLL_TIMEOUT_MS = 5 * 60 * 1000;

type Recommendation =
  | { id: string; kind: "negotiation"; item: NegotiationOutput }
  | { id: string; kind: "collections"; item: CollectionsOutput }
  | { id: string; kind: "lead_followup"; item: LeadFollowupOutput }
  | { id: string; kind: "crm_engagement"; item: CrmEngagementOutput }
  | { id: string; kind: "crm_deal_watch"; item: CrmDealWatchOutput };

type ReviewFilter =
  | "all"
  | "negotiation"
  | "collections"
  | "lead_followup"
  | "crm_engagement"
  | "crm_deal_watch"
  | "needs_review";

function formatMoney(value: number | undefined): string {
  if (value === undefined || Number.isNaN(value)) return "—";
  const whole = Number.isInteger(value);
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  });
}

function formatTime(value: string | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function humanise(value: string | undefined): string {
  return value ? value.replaceAll("_", " ") : "Not provided";
}

function buildRecommendations(briefing: AgentRunBriefing): Recommendation[] {
  return [
    ...briefing.negotiation.map((item, index) => ({
      id: `negotiation-${item.input_hash ?? item.quote_id ?? index}`,
      kind: "negotiation" as const,
      item,
    })),
    ...briefing.collections.map((item, index) => ({
      id: `collections-${item.input_hash ?? item.invoice_id ?? index}`,
      kind: "collections" as const,
      item,
    })),
    // optional: briefings saved before the lead agent existed have no section
    ...(briefing.lead_followup ?? []).map((item, index) => ({
      id: `lead_followup-${item.input_hash ?? item.lead_id ?? index}`,
      kind: "lead_followup" as const,
      item,
    })),
    // optional: briefings saved before the CRM agents existed have no sections
    ...(briefing.crm_engagement ?? []).map((item, index) => ({
      id: `crm_engagement-${item.input_hash ?? item.record_id ?? index}`,
      kind: "crm_engagement" as const,
      item,
    })),
    ...(briefing.crm_deal_watch ?? []).map((item, index) => ({
      id: `crm_deal_watch-${item.input_hash ?? item.record_id ?? index}`,
      kind: "crm_deal_watch" as const,
      item,
    })),
  ];
}

export function AgentReview() {
  const { briefing, setBriefing } = useLiveData();
  const [isLoading, setIsLoading] = useState(!briefing);
  const [isStartingRun, setIsStartingRun] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [copiedId, setCopiedId] = useState("");
  const [reviewedIds, setReviewedIds] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState<ReviewFilter>("all");
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const loadBriefing = useCallback(async () => {
    setIsLoading(true);
    setErrorMessage("");
    try {
      const result = await fetchLatestBriefing();
      if (!mountedRef.current) return;
      setBriefing(result.run_id === null ? null : result);
    } catch (error) {
      if (!mountedRef.current) return;
      setErrorMessage(
        error instanceof Error ? error.message : "Could not load the latest agent briefing.",
      );
    } finally {
      if (mountedRef.current) setIsLoading(false);
    }
  }, [setBriefing]);

  useEffect(() => {
    if (briefing) return;

    let isActive = true;
    fetchLatestBriefing()
      .then((result) => {
        if (!isActive) return;
        setBriefing(result.run_id === null ? null : result);
      })
      .catch((error: unknown) => {
        if (!isActive) return;
        setErrorMessage(
          error instanceof Error ? error.message : "Could not load the latest agent briefing.",
        );
      })
      .finally(() => {
        if (isActive) setIsLoading(false);
      });

    return () => {
      isActive = false;
    };
  }, [briefing, setBriefing]);

  async function waitForRun(runId: string) {
    const deadline = Date.now() + RUN_POLL_TIMEOUT_MS;
    while (Date.now() < deadline && mountedRef.current) {
      await new Promise((resolve) => setTimeout(resolve, RUN_POLL_INTERVAL_MS));
      if (!mountedRef.current) return;

      try {
        const result = await fetchLatestBriefing();
        if (!mountedRef.current) return;
        if (result.run_id === runId) {
          setBriefing(result);
          setStatusMessage("Agent run finished. This briefing is now in the review queue.");
          return;
        }
      } catch {
        // transient polling failure: keep waiting until the deadline
      }
    }

    if (mountedRef.current) {
      setStatusMessage(
        "The run is still going after 5 minutes. Press refresh later to pick up the briefing.",
      );
    }
  }

  async function handleStartRun() {
    if (isStartingRun) return;
    setIsStartingRun(true);
    setErrorMessage("");
    setStatusMessage("");

    try {
      const started = await startAgentRun();
      if (!mountedRef.current) return;
      if (started.status === "already_running") {
        setStatusMessage(
          started.run_id
            ? `Agent run ${started.run_id.slice(0, 8)} is already running. Waiting for it to finish...`
            : "An agent run is already running; it will show up when it finishes.",
        );
        if (started.run_id) await waitForRun(started.run_id);
        return;
      }
      const { run_id: runId } = started;
      setStatusMessage(
        `Agent run ${runId.slice(0, 8)} started. Waiting for the pipeline to finish...`,
      );
      await waitForRun(runId);
    } catch (error) {
      if (!mountedRef.current) return;
      setErrorMessage(
        error instanceof Error ? error.message : "Could not start an agent run.",
      );
    } finally {
      if (mountedRef.current) setIsStartingRun(false);
    }
  }

  async function handleCopyDraft(id: string, draft: string) {
    try {
      await navigator.clipboard.writeText(draft);
      setCopiedId(id);
      setErrorMessage("");
      window.setTimeout(() => {
        if (mountedRef.current) setCopiedId((current) => (current === id ? "" : current));
      }, 2000);
    } catch {
      setErrorMessage("Could not copy the draft. Check clipboard permissions and try again.");
    }
  }

  function markReviewed(id: string) {
    setReviewedIds((current) => ({ ...current, [id]: true }));
  }

  const recommendations = briefing ? buildRecommendations(briefing) : [];
  const visibleRecommendations = recommendations.filter((recommendation) => {
    if (filter === "all") return true;
    if (filter === "needs_review") {
      const flags = recommendation.item.flags ?? [];
      return flags.includes("needs_review") || flags.includes("error") || Boolean(recommendation.item.error);
    }
    return recommendation.kind === filter;
  });

  const master = briefing?.master ?? null;
  const digest = briefing?.crm_digest ?? null;

  return (
    <section aria-labelledby="review-title" className="space-y-6">
      {/* Top Header Card with Run Action & Refresh */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-border/80 bg-surface/80 p-5 shadow-sm backdrop-blur-md">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 id="review-title" className="text-base font-semibold tracking-tight text-foreground">
              AI review queue
            </h2>
            <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-raised px-2.5 py-0.5 text-[11px] font-medium text-muted">
              <Bot size={12} className="text-accent" aria-hidden="true" />
              Autonomous dispatch
            </span>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Human review of agent-generated drafts · read-only by design
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleStartRun()}
            disabled={isStartingRun}
            className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-xs font-semibold text-[#11150b] shadow-sm transition-all hover:opacity-90 active:scale-[0.98] disabled:cursor-wait disabled:opacity-60"
          >
            <Play size={13} aria-hidden="true" className={isStartingRun ? "animate-pulse" : ""} />
            {isStartingRun ? "Run in progress..." : "Run agents"}
          </button>
          <button
            type="button"
            onClick={() => void loadBriefing()}
            disabled={isLoading || isStartingRun}
            aria-label="Refresh the latest briefing"
            className="grid h-8 w-8 place-items-center rounded-xl border border-border/80 bg-surface-raised/60 text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-50"
          >
            <RefreshCw size={13} aria-hidden="true" className={isLoading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Prominent Read-Only Warning Banner — High Visual Weight */}
      <div className="relative overflow-hidden rounded-2xl border-2 border-warning-border bg-warning-muted/70 p-4.5 shadow-sm backdrop-blur-md">
        <div className="flex items-start gap-3.5">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-warning-muted border border-warning-border text-warning">
            <AlertTriangle size={17} aria-hidden="true" />
          </div>
          <div className="space-y-1">
            <h3 className="text-xs font-bold uppercase tracking-wider text-warning">
              Strictly Read-Only Operational Queue
            </h3>
            <p className="text-xs leading-relaxed text-warning-foreground">
              This queue drafts messages; nothing is sent. The backend exposes no approval or
              write endpoint (<code className="rounded bg-surface/60 px-1 py-0.5 font-mono text-[11px] font-semibold text-foreground">/api/approve</code> returns 404), so approve/reject buttons would be
              cosmetic. Marking a draft reviewed only affects this browser session.
            </p>
          </div>
        </div>
      </div>

      {errorMessage && (
        <div className="flex items-center gap-2 rounded-xl border border-negative-border bg-negative-muted px-4 py-3 text-xs text-negative" role="alert">
          <ShieldAlert size={15} aria-hidden="true" className="shrink-0" />
          <p>{errorMessage}</p>
        </div>
      )}
      {statusMessage && (
        <div className="flex items-center gap-2 rounded-xl border border-border/70 bg-surface-raised/40 px-4 py-2.5 text-xs text-muted" role="status">
          <Clock size={13} aria-hidden="true" className="shrink-0 text-accent" />
          <p>{statusMessage}</p>
        </div>
      )}

      {isLoading && !briefing ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-border/80 bg-surface/60 py-16 text-center" role="status">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          <p className="mt-3 text-sm text-muted">Loading the latest briefing...</p>
        </div>
      ) : !briefing ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border/80 bg-surface/60 py-16 text-center">
          <div className="grid h-12 w-12 place-items-center rounded-2xl bg-surface-raised text-accent">
            <Sparkles size={22} aria-hidden="true" />
          </div>
          <p className="mt-3 text-sm font-semibold text-foreground">No agent run yet</p>
          <p className="mt-1 max-w-sm text-xs text-muted">
            Start a run, or wait for the scheduler to trigger one after new ERP activity.
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Run Header: Metrics, timestamps, token count, and per-agent breakdown */}
          <div className="overflow-hidden rounded-2xl border border-border/80 bg-surface/80 p-5 shadow-sm backdrop-blur-md">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 pb-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-sm font-bold tracking-tight text-foreground">
                  Run #{briefing.run_id.slice(0, 8)}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-raised px-2.5 py-0.5 font-mono text-[11px] text-muted border border-border/60">
                  <Clock size={11} aria-hidden="true" />
                  Started {formatTime(briefing.started_at)}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-raised px-2.5 py-0.5 font-mono text-[11px] text-muted border border-border/60">
                  <Check size={11} aria-hidden="true" className="text-positive" />
                  Finished {formatTime(briefing.finished_at)}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={`inline-flex items-center px-2.5 py-0.5 rounded-full font-mono text-xs font-bold uppercase tracking-wider ${
                    briefing.counts.needs_review > 0
                      ? "border border-warning-border bg-warning-muted text-warning-foreground"
                      : "border border-positive-border bg-positive-muted text-positive"
                  }`}
                >
                  {briefing.counts.needs_review} needs review
                </span>
                {briefing.usage && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-surface-raised px-2.5 py-0.5 font-mono text-xs text-foreground/80">
                    <Coins size={12} className="text-muted" aria-hidden="true" />
                    {briefing.usage.total_tokens.toLocaleString("en-US")} tokens
                  </span>
                )}
              </div>
            </div>

            {/* Per-agent item counts pills */}
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-surface-raised/40 px-2.5 py-1 text-foreground/85">
                <span className="font-semibold text-accent">{briefing.counts.negotiation_total}</span>
                <span>negotiation items</span>
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-surface-raised/40 px-2.5 py-1 text-foreground/85">
                <span className="font-semibold text-accent">{briefing.counts.collections_total}</span>
                <span>collections items</span>
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-surface-raised/40 px-2.5 py-1 text-foreground/85">
                <span className="font-semibold text-accent">{briefing.counts.lead_followup_total ?? 0}</span>
                <span>lead follow-up items</span>
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-surface-raised/40 px-2.5 py-1 text-foreground/85">
                <span className="font-semibold text-accent">{briefing.counts.crm_engagement_total ?? 0}</span>
                <span>CRM engagement items</span>
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-surface-raised/40 px-2.5 py-1 text-foreground/85">
                <span className="font-semibold text-accent">{briefing.counts.crm_deal_watch_total ?? 0}</span>
                <span>CRM deal watch items</span>
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-surface-raised/40 px-2.5 py-1 text-muted">
                <span className="font-mono">{briefing.counts.cached}</span>
                <span>reused from cache</span>
              </span>
            </div>
          </div>

          {/* Run Errors Banner */}
          {briefing.errors.length > 0 && (
            <div className="rounded-2xl border border-warning-border bg-warning-muted/60 p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-warning">Run errors</p>
              <ul className="mt-2 space-y-1.5 text-xs text-warning-foreground font-mono">
                {briefing.errors.slice(0, 5).map((error) => (
                  <li key={error} className="flex items-start gap-1.5">
                    <span className="text-warning">•</span>
                    <span>{error}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Executive Summary */}
          <section aria-labelledby="summary-title" className="rounded-2xl border border-border/80 bg-surface/80 p-5 shadow-sm backdrop-blur-md">
            <h3 id="summary-title" className="text-sm font-semibold tracking-tight text-foreground">
              Executive summary
            </h3>
            {master?.bullets.length ? (
              <ul className="mt-3.5 space-y-2">
                {master.bullets.map((bullet, index) => (
                  <li key={`${index}-${bullet}`} className="flex items-start gap-2.5 text-sm leading-relaxed text-foreground/90">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    <span>{bullet}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted">
                {master?.error || "No executive summary was produced for this run."}
              </p>
            )}
            {master?.flags?.includes("needs_review") && (
              <div className="mt-3 rounded-xl border border-warning-border bg-warning-muted/40 px-3.5 py-2 text-xs text-warning-foreground">
                Flagged by validation: {master.rejected_reason || "the brief needs a human read."}
              </div>
            )}
            {master?.generated_by === "code_template" && (
              <p className="mt-2 text-xs text-muted italic">
                Written by code: the model brief failed validation even after one regeneration.
              </p>
            )}
            {master?.conflicts_resolved.map((conflict, index) => (
              <p key={`${index}-${conflict}`} className="mt-2 text-xs text-muted">
                Resolved conflict: {conflict}
              </p>
            ))}
          </section>

          {/* Priorities Cards */}
          {master?.priorities.length ? (
            <section aria-labelledby="priorities-title" className="space-y-3">
              <h3 id="priorities-title" className="text-sm font-semibold tracking-tight text-foreground">
                Priorities
              </h3>
              <div className="grid gap-3.5 md:grid-cols-2">
                {master.priorities.map((priority, index) => (
                  <article
                    key={`${priority.customer_name}-${index}`}
                    className="flex flex-col justify-between rounded-2xl border border-border/80 bg-surface-raised/40 p-4.5 transition-all hover:border-border"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <Building size={14} className="text-accent" aria-hidden="true" />
                        <h4 className="text-sm font-semibold text-foreground tracking-tight">
                          {priority.customer_name}
                        </h4>
                      </div>
                      <p className="mt-2 text-xs leading-relaxed text-muted">{priority.why}</p>
                    </div>
                    <div className="mt-3 flex items-start gap-1.5 border-t border-border/60 pt-3 text-xs leading-relaxed text-foreground/90">
                      <span className="font-semibold text-accent shrink-0">Next:</span>
                      <span>{priority.suggested_action}</span>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ) : null}

          {/* CRM Digest */}
          {digest && (
            <section aria-labelledby="crm-digest-title" className="space-y-3">
              <h3 id="crm-digest-title" className="text-sm font-semibold tracking-tight text-foreground">
                CRM digest · {digest.date}
              </h3>
              <div className="grid gap-3.5 md:grid-cols-3">
                <article className="rounded-2xl border border-border/80 bg-surface-raised/40 p-4.5 transition-all hover:border-border">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-accent">
                    Payments in (CRM)
                  </p>
                  <p className="mt-1 font-mono text-base font-bold text-foreground">
                    {digest.payments.count} payments
                    {digest.payments.total_received !== null
                      ? ` · ${formatMoney(digest.payments.total_received)}`
                      : ""}
                  </p>
                  <div className="mt-2.5 space-y-1 border-t border-border/60 pt-2 text-xs text-muted">
                    {digest.payments.top.map((row, index) => (
                      <p key={index} className="flex justify-between font-mono">
                        <span className="truncate">{row.customer ?? "Unknown customer"}</span>
                        <span>{row.amount != null ? formatMoney(row.amount) : ""}</span>
                      </p>
                    ))}
                  </div>
                </article>
                <article className="rounded-2xl border border-border/80 bg-surface-raised/40 p-4.5 transition-all hover:border-border">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-accent">
                    Waiting for a reply
                  </p>
                  <p className="mt-1 font-mono text-base font-bold text-foreground">
                    {digest.waiting.total} customers · {digest.waiting.whatsapp_total} on WhatsApp
                  </p>
                  <div className="mt-2.5 space-y-1 border-t border-border/60 pt-2 text-xs text-muted">
                    {digest.waiting.top.map((row, index) => (
                      <p key={index} className="flex justify-between font-mono">
                        <span className="truncate">{row.name ?? "Unknown"}</span>
                        <span>{row.channel ?? "—"} · {row.waiting_days ?? "—"}d</span>
                      </p>
                    ))}
                  </div>
                </article>
                <article className="rounded-2xl border border-border/80 bg-surface-raised/40 p-4.5 transition-all hover:border-border">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-accent">
                    Stalled deals
                  </p>
                  <p className="mt-1 font-mono text-base font-bold text-foreground">
                    {digest.stalled_deals.total} deals
                  </p>
                  <div className="mt-2.5 space-y-1 border-t border-border/60 pt-2 text-xs text-muted">
                    {digest.stalled_deals.top.map((row, index) => (
                      <p key={index} className="flex justify-between font-mono">
                        <span className="truncate">{row.name ?? "Unnamed deal"}</span>
                        <span>{row.amount != null ? formatMoney(row.amount) : ""} · {row.days_idle ?? "—"}d</span>
                      </p>
                    ))}
                  </div>
                </article>
              </div>
            </section>
          )}

          {/* Recommendations Section */}
          <section aria-labelledby="recommendations-title" className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 id="recommendations-title" className="text-sm font-semibold tracking-tight text-foreground">
                Recommendations
              </h3>
              <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Recommendation filters">
                {(
                  [
                    ["all", `All ${recommendations.length}`],
                    ["negotiation", "Negotiation"],
                    ["collections", "Collections"],
                    ["lead_followup", "Lead follow-up"],
                    ["crm_engagement", "CRM engagement"],
                    ["crm_deal_watch", "CRM deal watch"],
                    ["needs_review", "Needs review"],
                  ] as [ReviewFilter, string][]
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setFilter(value)}
                    className={`rounded-xl border px-3 py-1.5 text-xs font-medium transition-all ${
                      filter === value
                        ? "border-accent/40 bg-accent/15 text-accent shadow-sm"
                        : "border-border/70 bg-surface-raised/40 text-muted hover:border-border hover:text-foreground"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {recommendations.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-border/80 bg-surface/60 p-10 text-center text-sm text-muted">
                This run produced no agent recommendations.
              </div>
            ) : visibleRecommendations.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-border/80 bg-surface/60 p-10 text-center text-sm text-muted">
                No recommendations match this filter.
              </div>
            ) : (
              <div className="space-y-4">
                {visibleRecommendations.map((recommendation) => (
                  <RecommendationCard
                    key={recommendation.id}
                    recommendation={recommendation}
                    reviewed={Boolean(reviewedIds[recommendation.id])}
                    copied={copiedId === recommendation.id}
                    onCopy={handleCopyDraft}
                    onMarkReviewed={markReviewed}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

function RecommendationCard({
  recommendation,
  reviewed,
  copied,
  onCopy,
  onMarkReviewed,
}: {
  recommendation: Recommendation;
  reviewed: boolean;
  copied: boolean;
  onCopy: (id: string, draft: string) => void;
  onMarkReviewed: (id: string) => void;
}) {
  const { id, kind, item } = recommendation;
  const isNegotiation = kind === "negotiation";
  const isLead = kind === "lead_followup";
  const isEngagement = kind === "crm_engagement";
  const isDeal = kind === "crm_deal_watch";
  const isCrm = isEngagement || isDeal;
  const negotiation = isNegotiation ? (item as NegotiationOutput) : null;
  const collections = kind === "collections" ? (item as CollectionsOutput) : null;
  const lead = isLead ? (item as LeadFollowupOutput) : null;
  const engagement = isEngagement ? (item as CrmEngagementOutput) : null;
  const deal = isDeal ? (item as CrmDealWatchOutput) : null;
  const amount = negotiation ? negotiation.quote_amount : collections?.balance;
  const flags = item.flags ?? [];
  const needsReview = flags.includes("needs_review") || flags.includes("error");
  const heading = isLead
    ? lead?.contact_name || lead?.company || "Lead unavailable"
    : isEngagement
      ? engagement?.contact_name || engagement?.company || "CRM record unavailable"
      : isDeal
        ? deal?.deal_name || deal?.account_name || "CRM deal unavailable"
        : item.customer_name || "Customer unavailable";
  const subheading = isLead
    ? [lead?.company, lead?.status, `${lead?.days_since_activity ?? "—"}d since activity`]
        .filter(Boolean)
        .join(" · ")
    : isEngagement
      ? [
          engagement?.company,
          engagement?.record_type,
          engagement?.channel ? `waiting on ${engagement.channel}` : undefined,
          `${engagement?.waiting_days ?? "—"}d waiting for a reply`,
        ]
          .filter(Boolean)
          .join(" · ")
      : isDeal
        ? [
            deal?.account_name,
            humanise(deal?.deal_kind),
            `${deal?.days_idle ?? "—"}d idle`,
            deal?.amount !== undefined ? formatMoney(deal.amount) : undefined,
          ]
            .filter(Boolean)
            .join(" · ")
        : isNegotiation
          ? `Quote ${negotiation?.quote_id ?? "—"}${amount !== undefined ? ` · ${formatMoney(amount)}` : ""}`
          : `Invoice ${collections?.invoice_id ?? "—"}${amount !== undefined ? ` · ${formatMoney(amount)}` : ""}`;

  return (
    <article className="overflow-hidden rounded-2xl border border-border/80 bg-surface/80 p-5 shadow-sm backdrop-blur-md transition-all hover:border-border">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <span className="inline-flex items-center rounded-md bg-accent/15 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider text-accent border border-accent/25">
            {isLead
              ? "Lead follow-up"
              : isNegotiation
                ? "Negotiation"
                : isEngagement
                  ? "CRM engagement"
                  : isDeal
                    ? "CRM deal watch"
                    : "Collections"}
          </span>
          <h4 className="mt-1.5 text-base font-semibold tracking-tight text-foreground">{heading}</h4>
          <p className="mt-0.5 text-xs font-mono text-muted">{subheading}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          {item.confidence && (
            <span className="rounded-full border border-border/60 bg-surface-raised px-2.5 py-0.5 text-muted font-mono">
              {item.confidence} confidence
            </span>
          )}
          <span className="rounded-full border border-border/60 bg-surface-raised px-2.5 py-0.5 text-muted font-mono">
            {item.source === "cache" ? "cached" : "fresh"}
          </span>
          {reviewed && (
            <span className="rounded-full border border-accent/30 bg-accent/15 px-2.5 py-0.5 font-semibold text-accent">
              Reviewed here
            </span>
          )}
          {flags.map((flag) => (
            <span
              key={flag}
              className="rounded-full border border-warning-border bg-warning-muted px-2.5 py-0.5 font-semibold text-warning-foreground"
            >
              {humanise(flag)}
            </span>
          ))}
        </div>
      </div>

      {isLead ? (
        <>
          {lead?.missing && lead.missing.length > 0 && (
            <div className="mt-3.5 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Missing:</span>
              {lead.missing.map((field) => (
                <span
                  key={field}
                  className="rounded-md border border-warning-border bg-warning-muted px-2 py-0.5 font-mono text-[11px] font-semibold text-warning-foreground"
                >
                  {field.replaceAll("_", " ")}
                </span>
              ))}
            </div>
          )}
          <div className="mt-3.5 grid gap-2.5 text-xs sm:grid-cols-2">
            <p>
              <span className="text-muted">Blocker: </span>
              <span className="font-medium text-foreground">{lead?.blocker || "Not provided"}</span>
            </p>
            <p>
              <span className="text-muted">Owner team: </span>
              <span className="font-medium text-foreground">{humanise(lead?.owner_team)}</span>
            </p>
          </div>
          {lead?.action_plan && (
            <p className="mt-2 text-xs leading-relaxed">
              <span className="text-muted">Action plan: </span>
              <span className="text-foreground/90">{lead.action_plan}</span>
            </p>
          )}
          {lead?.note && <p className="mt-1.5 text-xs leading-relaxed text-muted">Note: {lead.note}</p>}
        </>
      ) : isCrm ? (
        <div className="mt-3.5 grid gap-2 text-xs">
          {isEngagement && engagement?.last_customer_message && (
            <p className="italic text-foreground/90">
              <span className="text-muted not-italic">Customer wrote: </span>“
              {engagement.last_customer_message}”
            </p>
          )}
          <p>
            <span className="text-muted">{isEngagement ? "Situation: " : "Stall reason: "}</span>
            <span className="font-medium text-foreground">
              {(isEngagement ? engagement?.situation : deal?.stall_reason) || "Not provided"}
            </span>
          </p>
          {(isEngagement ? engagement?.action_plan : deal?.action_plan) && (
            <p>
              <span className="text-muted">Action plan: </span>
              <span className="text-foreground/90">
                {isEngagement ? engagement?.action_plan : deal?.action_plan}
              </span>
            </p>
          )}
        </div>
      ) : (
        <div className="mt-3.5 grid gap-2 text-xs sm:grid-cols-2">
          <p>
            <span className="text-muted">Recommended tactic: </span>
            <span className="font-medium text-foreground">
              {humanise(negotiation?.tactic ?? collections?.approach)}
              {collections?.tone ? ` (${collections.tone})` : ""}
            </span>
          </p>
          <p>
            <span className="text-muted">
              {isNegotiation ? "Likely reason: " : "Confidence evidence: "}
            </span>
            <span className="font-medium text-foreground">
              {isNegotiation ? humanise(negotiation?.likely_reason) : item.evidence || "—"}
            </span>
          </p>
        </div>
      )}

      {collections?.reason && (
        <p className="mt-2 text-xs leading-relaxed">
          <span className="text-muted">Reason: </span>
          <span className="text-foreground/90">{collections.reason}</span>
        </p>
      )}
      {item.evidence && (
        <p className="mt-1.5 text-xs leading-relaxed text-muted">Evidence: {item.evidence}</p>
      )}
      {(item.error || item.rejected_reason) && (
        <p className="mt-2 text-xs font-medium text-negative">
          {item.error || item.rejected_reason}
        </p>
      )}

      {/* Draft Message Card */}
      <div className="mt-4 rounded-xl border border-border/80 bg-surface-raised/60 p-4">
        <p className="text-[11px] font-bold uppercase tracking-wider text-muted">Draft message</p>
        <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-foreground/95 font-sans">
          {item.message_draft || "No draft available; this item needs human review."}
        </p>
      </div>

      {/* Internal Escalation Note */}
      {collections?.escalation_note && (
        <div className="mt-3 rounded-xl border border-warning-border bg-warning-muted/50 p-4">
          <p className="text-[11px] font-bold uppercase tracking-wider text-warning">
            Internal escalation note
          </p>
          <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-warning-foreground">
            {collections.escalation_note}
          </p>
        </div>
      )}

      {/* Card Actions */}
      <div className="mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-border/60 pt-3">
        <button
          type="button"
          onClick={() => item.message_draft && void onCopy(id, item.message_draft)}
          disabled={!item.message_draft}
          className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-surface px-3 py-1.5 text-xs font-semibold text-foreground/90 transition-all hover:border-accent/40 hover:text-accent active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {copied ? <Check size={13} className="text-positive" aria-hidden="true" /> : <Clipboard size={13} aria-hidden="true" />}
          <span>{copied ? "Copied" : "Copy draft"}</span>
        </button>
        <button
          type="button"
          onClick={() => onMarkReviewed(id)}
          disabled={reviewed || needsReview}
          title={needsReview ? "Flagged items must be fixed before they can be reviewed." : undefined}
          className="inline-flex items-center gap-1.5 rounded-xl bg-accent px-3.5 py-1.5 text-xs font-semibold text-[#11150b] shadow-sm transition-all hover:opacity-90 active:scale-[0.98] disabled:cursor-default disabled:opacity-50"
        >
          <Check size={13} aria-hidden="true" />
          <span>{reviewed ? "Reviewed here" : "Mark reviewed"}</span>
        </button>
      </div>
    </article>
  );
}

export const ApprovalInbox = AgentReview;
