"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  Clipboard,
  Play,
  RefreshCw,
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

  return (
    <section aria-labelledby="review-title" className="rounded-xl border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div>
          <h2 id="review-title" className="font-semibold">AI review queue</h2>
          <p className="mt-1 text-xs text-muted">
            Human review of agent-generated drafts · read-only by design
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void handleStartRun()}
            disabled={isStartingRun}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs font-medium text-[#11150b] transition-opacity hover:opacity-90 disabled:cursor-wait disabled:opacity-60"
          >
            <Play size={13} aria-hidden="true" />
            {isStartingRun ? "Run in progress..." : "Run agents"}
          </button>
          <button
            type="button"
            onClick={() => void loadBriefing()}
            disabled={isLoading || isStartingRun}
            aria-label="Refresh the latest briefing"
            className="grid h-8 w-8 place-items-center rounded-md border border-border text-muted transition-colors hover:bg-surface-raised hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="space-y-5 p-5">
        <div className="flex gap-2 rounded-lg border border-amber-400/20 bg-amber-400/5 px-4 py-3">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
          <p className="text-xs leading-5 text-amber-300">
            This queue drafts messages; nothing is sent. The backend exposes no approval or
            write endpoint (`/api/approve` returns 404), so approve/reject buttons would be
            cosmetic. Marking a draft reviewed only affects this browser session.
          </p>
        </div>

        {errorMessage && (
          <p className="text-sm text-red-400" role="alert">{errorMessage}</p>
        )}
        {statusMessage && (
          <p className="text-xs text-muted" role="status">{statusMessage}</p>
        )}

        {isLoading && !briefing ? (
          <p className="py-6 text-center text-sm text-muted" role="status">
            Loading the latest briefing...
          </p>
        ) : !briefing ? (
          <div className="py-6 text-center">
            <Sparkles className="mx-auto text-accent" size={22} aria-hidden="true" />
            <p className="mt-3 text-sm font-medium">No agent run yet</p>
            <p className="mt-1 text-xs text-muted">
              Start a run, or wait for the scheduler to trigger one after new ERP activity.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted">
              <span>Run {briefing.run_id.slice(0, 8)}</span>
              <span>Started {formatTime(briefing.started_at)}</span>
              <span>Finished {formatTime(briefing.finished_at)}</span>
              <span>{briefing.counts.negotiation_total} negotiation items</span>
              <span>{briefing.counts.collections_total} collections items</span>
              <span>{briefing.counts.lead_followup_total ?? 0} lead follow-up items</span>
              <span>{briefing.counts.crm_engagement_total ?? 0} CRM engagement items</span>
              <span>{briefing.counts.crm_deal_watch_total ?? 0} CRM deal watch items</span>
              <span>{briefing.counts.cached} reused from cache</span>
              <span
                className={
                  briefing.counts.needs_review > 0 ? "text-amber-300" : "text-emerald-400"
                }
              >
                {briefing.counts.needs_review} needs review
              </span>
              {briefing.usage && (
                <span>{briefing.usage.total_tokens.toLocaleString("en-US")} tokens</span>
              )}
            </div>

            {briefing.errors.length > 0 && (
              <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-4 py-3">
                <p className="text-xs font-medium text-amber-300">Run errors</p>
                <ul className="mt-1 space-y-1 text-xs text-amber-300/90">
                  {briefing.errors.slice(0, 5).map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </div>
            )}

            <section aria-labelledby="summary-title">
              <h3 id="summary-title" className="text-sm font-semibold">Executive summary</h3>
              {master?.bullets.length ? (
                <ul className="mt-3 space-y-2">
                  {master.bullets.map((bullet, index) => (
                    <li key={`${index}-${bullet}`} className="flex gap-2 text-sm leading-5">
                      <span className="text-accent">•</span>
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
                <p className="mt-2 text-xs text-amber-300">
                  Flagged by validation: {master.rejected_reason || "the brief needs a human read."}
                </p>
              )}
              {master?.generated_by === "code_template" && (
                <p className="mt-2 text-xs text-muted">
                  Written by code: the model brief failed validation even after one regeneration.
                </p>
              )}
              {master?.conflicts_resolved.map((conflict, index) => (
                <p key={`${index}-${conflict}`} className="mt-2 text-xs text-muted">
                  Resolved conflict: {conflict}
                </p>
              ))}
            </section>

            {master?.priorities.length ? (
              <section aria-labelledby="priorities-title">
                <h3 id="priorities-title" className="text-sm font-semibold">Priorities</h3>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  {master.priorities.map((priority, index) => (
                    <article
                      key={`${priority.customer_name}-${index}`}
                      className="rounded-lg border border-border bg-background p-4"
                    >
                      <p className="text-sm font-medium">{priority.customer_name}</p>
                      <p className="mt-2 text-xs leading-5 text-muted">{priority.why}</p>
                      <p className="mt-2 text-xs leading-5">
                        <span className="text-muted">Next: </span>
                        {priority.suggested_action}
                      </p>
                    </article>
                  ))}
                </div>
              </section>
            ) : null}

            <section aria-labelledby="recommendations-title">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 id="recommendations-title" className="text-sm font-semibold">
                  Recommendations
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {                  (
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
                      className={`rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
                        filter === value
                          ? "border-accent/40 bg-accent/10 text-accent"
                          : "border-border text-muted hover:text-foreground"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {recommendations.length === 0 ? (
                <p className="mt-3 rounded-lg border border-border px-4 py-6 text-center text-sm text-muted">
                  This run produced no agent recommendations.
                </p>
              ) : visibleRecommendations.length === 0 ? (
                <p className="mt-3 rounded-lg border border-border px-4 py-6 text-center text-sm text-muted">
                  No recommendations match this filter.
                </p>
              ) : (
                <div className="mt-3 space-y-3">
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
          </>
        )}
      </div>
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
      ? [engagement?.company, engagement?.record_type, `${engagement?.waiting_days ?? "—"}d waiting for a reply`]
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
    <article className="rounded-lg border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-accent">
            {isLead
              ? "Lead follow-up"
              : isNegotiation
                ? "Negotiation"
                : isEngagement
                  ? "CRM engagement"
                  : isDeal
                    ? "CRM deal watch"
                    : "Collections"}
          </p>
          <h4 className="mt-1 font-medium">{heading}</h4>
          <p className="mt-1 text-xs text-muted">{subheading}</p>
        </div>
        <div className="flex flex-wrap gap-2 text-[11px]">
          {item.confidence && (
            <span className="rounded-full bg-surface-raised px-2.5 py-1 text-muted">
              {item.confidence} confidence
            </span>
          )}
          <span className="rounded-full bg-surface-raised px-2.5 py-1 text-muted">
            {item.source === "cache" ? "cached" : "fresh"}
          </span>
          {reviewed && (
            <span className="rounded-full bg-accent/10 px-2.5 py-1 text-accent">Reviewed here</span>
          )}
          {flags.map((flag) => (
            <span
              key={flag}
              className="rounded-full bg-amber-400/10 px-2.5 py-1 text-amber-300"
            >
              {humanise(flag)}
            </span>
          ))}
        </div>
      </div>

      {isLead ? (
        <>
          {lead?.missing && lead.missing.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] uppercase tracking-wide text-muted">Missing:</span>
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
          <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
            <p>
              <span className="text-muted">Blocker: </span>
              {lead?.blocker || "Not provided"}
            </p>
            <p>
              <span className="text-muted">Owner team: </span>
              {humanise(lead?.owner_team)}
            </p>
          </div>
          {lead?.action_plan && (
            <p className="mt-2 text-sm leading-5">
              <span className="text-muted">Action plan: </span>
              {lead.action_plan}
            </p>
          )}
          {lead?.note && <p className="mt-2 text-xs leading-5 text-muted">Note: {lead.note}</p>}
        </>
      ) : isCrm ? (
        <div className="mt-3 grid gap-2 text-sm">
          <p>
            <span className="text-muted">{isEngagement ? "Situation: " : "Stall reason: "}</span>
            {(isEngagement ? engagement?.situation : deal?.stall_reason) || "Not provided"}
          </p>
          {(isEngagement ? engagement?.action_plan : deal?.action_plan) && (
            <p>
              <span className="text-muted">Action plan: </span>
              {isEngagement ? engagement?.action_plan : deal?.action_plan}
            </p>
          )}
        </div>
      ) : (
        <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
          <p>
            <span className="text-muted">Recommended tactic: </span>
            {humanise(negotiation?.tactic ?? collections?.approach)}
            {collections?.tone ? ` (${collections.tone})` : ""}
          </p>
          <p>
            <span className="text-muted">
              {isNegotiation ? "Likely reason: " : "Confidence evidence: "}
            </span>
            {isNegotiation ? humanise(negotiation?.likely_reason) : item.evidence || "—"}
          </p>
        </div>
      )}

      {collections?.reason && (
        <p className="mt-2 text-sm leading-5">
          <span className="text-muted">Reason: </span>
          {collections.reason}
        </p>
      )}
      {item.evidence && (
        <p className="mt-2 text-xs leading-5 text-muted">Evidence: {item.evidence}</p>
      )}
      {(item.error || item.rejected_reason) && (
        <p className="mt-2 text-xs text-red-400">
          {item.error || item.rejected_reason}
        </p>
      )}

      <div className="mt-4 rounded-md bg-surface-raised/60 p-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted">Draft message</p>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-5">
          {item.message_draft || "No draft available; this item needs human review."}
        </p>
      </div>

      {collections?.escalation_note && (
        <div className="mt-3 rounded-md border border-amber-400/30 bg-amber-400/5 p-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-amber-300">
            Internal escalation note
          </p>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-5">{collections.escalation_note}</p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={() => item.message_draft && void onCopy(id, item.message_draft)}
          disabled={!item.message_draft}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-surface-raised hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          {copied ? <Check size={13} aria-hidden="true" /> : <Clipboard size={13} aria-hidden="true" />}
          {copied ? "Copied" : "Copy draft"}
        </button>
        <button
          type="button"
          onClick={() => onMarkReviewed(id)}
          disabled={reviewed || needsReview}
          title={needsReview ? "Flagged items must be fixed before they can be reviewed." : undefined}
          className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-[#11150b] transition-opacity hover:opacity-90 disabled:cursor-default disabled:opacity-60"
        >
          <Check size={13} aria-hidden="true" />
          {reviewed ? "Reviewed here" : "Mark reviewed"}
        </button>
      </div>
    </article>
  );
}

export const ApprovalInbox = AgentReview;
