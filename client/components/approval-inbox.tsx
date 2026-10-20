"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Clipboard, Play, RefreshCw, Sparkles } from "lucide-react";
import { fetchLatestBriefing, startAgentRun } from "@/services/api";
import type {
  AgentConfidence,
  AgentRunBriefing,
  CollectionsOutput,
  NegotiationOutput,
} from "@/types/api";

interface RecommendationCardProps {
  id: string;
  title: string;
  customerName: string | null;
  confidence?: AgentConfidence;
  reason: string;
  evidence?: string;
  approach: string;
  draft: string | null;
  flags: string[];
  error?: string;
  reviewed: boolean;
  copied: boolean;
  onCopy: (id: string, draft: string) => void;
  onMarkReviewed: (id: string) => void;
}

function RecommendationCard({
  id,
  title,
  customerName,
  confidence,
  reason,
  evidence,
  approach,
  draft,
  flags,
  error,
  reviewed,
  copied,
  onCopy,
  onMarkReviewed,
}: RecommendationCardProps) {
  return (
    <article className="rounded-lg border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-accent">{title}</p>
          <h4 className="mt-1 font-medium">{customerName || "Customer unavailable"}</h4>
        </div>
        <div className="flex flex-wrap gap-2 text-[11px]">
          {confidence && (
            <span className="rounded-full bg-surface-raised px-2.5 py-1 text-muted">
              {confidence} confidence
            </span>
          )}
          {reviewed && (
            <span className="rounded-full bg-accent/10 px-2.5 py-1 text-accent">
              Reviewed
            </span>
          )}
          {flags.length > 0 && (
            <span className="rounded-full bg-amber-400/10 px-2.5 py-1 text-amber-300">
              {flags.join(", ").replaceAll("_", " ")}
            </span>
          )}
        </div>
      </div>
      <p className="mt-3 text-sm">
        <span className="text-muted">Recommendation: </span>{approach}
      </p>
      {reason && (
        <p className="mt-2 text-sm leading-5">
          <span className="text-muted">Reason: </span>{reason}
        </p>
      )}
      {evidence && (
        <p className="mt-2 text-xs leading-5 text-muted">
          Evidence: {evidence}
        </p>
      )}
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      <div className="mt-4 rounded-md bg-surface-raised/60 p-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted">Draft message</p>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-5">
          {draft || "No draft available; this item needs human review."}
        </p>
      </div>
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={() => draft && void onCopy(id, draft)}
          disabled={!draft}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-surface-raised hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          {copied ? <Check size={13} aria-hidden="true" /> : <Clipboard size={13} aria-hidden="true" />}
          {copied ? "Copied" : "Copy draft"}
        </button>
        <button
          type="button"
          onClick={() => onMarkReviewed(id)}
          disabled={reviewed}
          className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-[#11150b] transition-opacity hover:opacity-90 disabled:cursor-default disabled:opacity-60"
        >
          <Check size={13} aria-hidden="true" />
          {reviewed ? "Reviewed" : "Mark as reviewed"}
        </button>
      </div>
    </article>
  );
}

function RecommendationList({
  briefing,
  reviewedItems,
  copiedItem,
  onCopy,
  onMarkReviewed,
}: {
  briefing: AgentRunBriefing;
  reviewedItems: Record<string, boolean>;
  copiedItem: string;
  onCopy: (id: string, draft: string) => void;
  onMarkReviewed: (id: string) => void;
}) {
  const recommendations = [
    ...briefing.negotiation.map((item, index) => ({
      id: `negotiation-${item.quote_id ?? index}`,
      type: "negotiation" as const,
      item,
    })),
    ...briefing.collections.map((item, index) => ({
      id: `collections-${item.invoice_id ?? index}`,
      type: "collections" as const,
      item,
    })),
  ];

  if (recommendations.length === 0) {
    return (
      <p className="rounded-lg border border-border px-4 py-6 text-center text-sm text-muted">
        This run has no negotiation or collections recommendations.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {recommendations.map(({ id, type, item }) => {
        if (type === "negotiation" && "quote_id" in item) {
          const negotiation: NegotiationOutput = item;
          return (
            <RecommendationCard
              key={id}
              id={id}
              title={`Quote ${negotiation.quote_id ?? "recommendation"}`}
              customerName={negotiation.customer_name ?? null}
              confidence={negotiation.confidence}
              reason={negotiation.likely_reason?.replaceAll("_", " ") ?? ""}
              evidence={negotiation.evidence}
              approach={negotiation.tactic?.replaceAll("_", " ") ?? "Not provided"}
              draft={negotiation.message_draft ?? null}
              flags={negotiation.flags ?? []}
              error={negotiation.error ?? negotiation.rejected_reason}
              reviewed={Boolean(reviewedItems[id])}
              copied={copiedItem === id}
              onCopy={onCopy}
              onMarkReviewed={onMarkReviewed}
            />
          );
        }

        if (!("invoice_id" in item)) return null;
        const collections: CollectionsOutput = item;
        return (
          <RecommendationCard
            key={id}
            id={id}
            title={`Invoice ${collections.invoice_id ?? "recommendation"}`}
            customerName={collections.customer_name ?? null}
            confidence={collections.confidence}
            reason={collections.reason ?? ""}
            evidence={collections.evidence}
            approach={collections.approach?.replaceAll("_", " ") ?? "Not provided"}
            draft={collections.message_draft ?? null}
            flags={collections.flags ?? []}
            error={collections.error ?? collections.rejected_reason}
            reviewed={Boolean(reviewedItems[id])}
            copied={copiedItem === id}
            onCopy={onCopy}
            onMarkReviewed={onMarkReviewed}
          />
        );
      })}
    </div>
  );
}

export function AgentBriefings() {
  const [briefing, setBriefing] = useState<AgentRunBriefing | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isStartingRun, setIsStartingRun] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [runMessage, setRunMessage] = useState("");
  const [copyMessage, setCopyMessage] = useState("");
  const [copiedItem, setCopiedItem] = useState("");
  const [reviewedItems, setReviewedItems] = useState<Record<string, boolean>>({});

  const loadBriefing = useCallback(async () => {
    setIsLoading(true);
    setErrorMessage("");
    try {
      const result = await fetchLatestBriefing();
      setBriefing(result.run_id === null ? null : result);
    } catch (error) {
      console.error("Unable to load the latest agent briefing.", error);
      setErrorMessage(
        error instanceof Error ? error.message : "Could not load agent briefings.",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    let isActive = true;
    fetchLatestBriefing()
      .then((result) => {
        if (isActive) setBriefing(result.run_id === null ? null : result);
      })
      .catch((error: unknown) => {
        console.error("Unable to load the latest agent briefing.", error);
        if (isActive) {
          setErrorMessage(
            error instanceof Error ? error.message : "Could not load agent briefings.",
          );
        }
      })
      .finally(() => {
        if (isActive) setIsLoading(false);
      });

    return () => {
      isActive = false;
    };
  }, []);

  async function handleStartRun() {
    if (isStartingRun) return;
    setIsStartingRun(true);
    setErrorMessage("");
    setRunMessage("");
    try {
      const result = await startAgentRun();
      setRunMessage(`Agent run started (${result.run_id}). Refresh the briefing after it completes.`);
    } catch (error) {
      console.error("Unable to start an agent run.", error);
      setErrorMessage(
        error instanceof Error ? error.message : "Could not start an agent run.",
      );
    } finally {
      setIsStartingRun(false);
    }
  }

  async function handleCopyDraft(id: string, draft: string) {
    setCopyMessage("");
    try {
      await navigator.clipboard.writeText(draft);
      setCopiedItem(id);
      setCopyMessage("");
    } catch (error) {
      console.error("Unable to copy recommendation draft.", error);
      setCopyMessage("Could not copy the draft. Check clipboard permissions and try again.");
    }
  }

  function handleMarkReviewed(id: string) {
    setReviewedItems((current) => ({ ...current, [id]: true }));
  }

  const bullets = briefing?.master?.bullets ?? [];

  return (
    <section aria-labelledby="briefings-title" className="rounded-xl border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div>
          <h2 id="briefings-title" className="font-semibold">Agent briefings</h2>
          <p className="mt-1 text-xs text-muted">Human review of AI-generated recommendations</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void handleStartRun()}
            disabled={isStartingRun}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs font-medium text-[#11150b] disabled:opacity-60"
          >
            <Play size={13} aria-hidden="true" />
            {isStartingRun ? "Starting..." : "Run agents"}
          </button>
          <button
            type="button"
            onClick={() => void loadBriefing()}
            disabled={isLoading}
            aria-label="Refresh latest briefing"
            className="grid h-8 w-8 place-items-center rounded-md border border-border text-muted hover:bg-surface-raised hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="space-y-5 p-5">
        {errorMessage && <p className="text-sm text-red-400" role="alert">{errorMessage}</p>}
        {runMessage && <p className="text-xs text-muted" role="status">{runMessage}</p>}
        {copyMessage && <p className="text-xs text-red-400" role="alert">{copyMessage}</p>}

        {isLoading ? (
          <p className="py-6 text-center text-sm text-muted" role="status">Loading latest briefing...</p>
        ) : !briefing ? (
          <div className="py-6 text-center">
            <Sparkles className="mx-auto text-accent" size={22} aria-hidden="true" />
            <p className="mt-3 text-sm font-medium">No agent briefing yet</p>
            <p className="mt-1 text-xs text-muted">Start an agent run to generate recommendations for review.</p>
          </div>
        ) : (
          <>
            <div>
              <h3 className="text-sm font-semibold">Executive summary</h3>
              {bullets.length > 0 ? (
                <ul className="mt-3 space-y-2">
                  {bullets.map((bullet, index) => (
                    <li key={`${index}-${bullet}`} className="flex gap-2 text-sm leading-5">
                      <span className="text-accent">•</span>
                      <span>{bullet}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-muted">
                  {briefing?.master?.error || "No executive summary was produced for this run."}
                </p>
              )}
              {briefing?.master?.conflicts_resolved.map((conflict, index) => (
                <p key={`${index}-${conflict}`} className="mt-2 text-xs text-muted">
                  Resolved conflict: {conflict}
                </p>
              ))}
            </div>

            <div className="border-t border-border pt-4">
              <div className="mb-3 flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">Recommendations</h3>
                <span className="text-[11px] text-muted">
                  Run {briefing?.run_id.slice(0, 8)}
                </span>
              </div>
              <RecommendationList
                briefing={briefing}
                reviewedItems={reviewedItems}
                copiedItem={copiedItem}
                onCopy={handleCopyDraft}
                onMarkReviewed={handleMarkReviewed}
              />
            </div>
          </>
        )}
      </div>
    </section>
  );
}

export const ApprovalInbox = AgentBriefings;
