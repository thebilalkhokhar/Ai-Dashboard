"use client";

// The activity feed. Each row is one persisted event, phrased for an
// executive; the raw event names, ids and payloads stay behind an optional
// diagnostics disclosure so the main view never becomes a wall of logs.

import { formatTime } from "@/lib/format";
import { agentLabel } from "@/lib/presentation";
import type { TimelineEntry } from "@/lib/task-events";

function tone(entry: TimelineEntry): "working" | "done" | "failed" | "cached" | "neutral" {
  if (entry.type === "agent.failed" || entry.type === "task.failed" || entry.type === "erp.sync_failed") {
    return "failed";
  }
  if (entry.summary.toLowerCase().includes("cached")) return "cached";
  if (entry.type === "agent.completed" || entry.type === "task.completed") return "done";
  if (entry.type === "agent.started" || entry.type === "agent.progress" || entry.type === "tool.started") {
    return "working";
  }
  return "neutral";
}

export function ActivityTimeline({
  entries,
  details = {},
  showDiagnostics = false,
  emptyMessage = "Waiting for the assistant to start.",
}: {
  entries: TimelineEntry[];
  details?: Record<string, Record<string, unknown>>;
  showDiagnostics?: boolean;
  emptyMessage?: string;
}) {
  if (entries.length === 0) {
    return <p className="jfeed-row text-muted" role="status">{emptyMessage}</p>;
  }

  return (
    <ol className="jfeed" aria-label="Activity">
      {entries.map((entry) => (
        <li key={entry.event_id} className="jfeed-row" data-tone={tone(entry)}>
          <span className="jfeed-text">
            <span className="sr-only">{`${agentLabel(entry.agent_id)}: `}</span>
            {entry.human}
            {entry.durationMs !== undefined ? ` · ${entry.durationMs} ms` : ""}
          </span>
          <span className="jfeed-meta">{formatTime(entry.ts)}</span>
        </li>
      ))}
      {showDiagnostics && <Diagnostics entries={entries} details={details} />}
    </ol>
  );
}

function Diagnostics({
  entries,
  details,
}: {
  entries: TimelineEntry[];
  details: Record<string, Record<string, unknown>>;
}) {
  return (
    <li className="cc-diag">
      <details>
        <summary>Technical details</summary>
        <ul className="cc-diag-list">
          {entries.map((entry) => (
            <li key={`diag-${entry.event_id}`}>
              {`#${entry.seq} ${entry.type}${entry.agent_id ? ` [${entry.agent_id}]` : ""} ${entry.summary}`}
            </li>
          ))}
          {Object.keys(details).length > 0 && (
            <li>{`${Object.keys(details).length} event payloads recorded`}</li>
          )}
        </ul>
      </details>
    </li>
  );
}
