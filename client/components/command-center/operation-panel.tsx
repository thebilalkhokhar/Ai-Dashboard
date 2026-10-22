"use client";

// The live operation panel: the request, the phase the backend evidence
// supports, the assigned assistant when one has reported, and the latest real
// activity. It never shows ids, raw event names or payloads.

import { useState } from "react";
import { Activity, ChevronDown, ChevronUp } from "lucide-react";
import { ActivityTimeline } from "@/components/command-center/activity-timeline";
import type { ActiveTurn } from "@/components/command-center/execution-context";
import { deriveOperation } from "@/lib/presentation";
import type { AgentNodeState, ExecutionPhase, TimelineEntry } from "@/lib/task-events";
import { isLivePhase } from "@/lib/task-events";

export function OperationPanel({
  turn,
  execution,
  timeline,
  agents,
  streamStatus,
}: {
  turn: ActiveTurn | null;
  execution: ExecutionPhase;
  timeline: TimelineEntry[];
  agents: Record<string, AgentNodeState>;
  streamStatus: string;
}) {
  const operation = deriveOperation({ turn, execution, timeline, agents });
  const active = Boolean(turn) && isLivePhase(execution);
  // The strip stays one line tall unless the reader opens it. It deliberately
  // does NOT swing open on its own while a request runs: doing so pushed the
  // conversation below the fold during the one moment the reader needs it most.
  // The live detail is already on screen - in the operation flow and the
  // activity feed - so nothing is hidden, only un-duplicated.
  const [open, setOpen] = useState(false);
  const namesAgent = operation.agent
    ? operation.phase.toLowerCase().includes(operation.agent.toLowerCase())
    : false;

  return (
    <section aria-label="Current operation" className="cc-operation jglass" data-open={open ? "true" : "false"}>
      <header className="cc-operation-head">
        <span className="cc-operation-title">
          <Activity size={13} aria-hidden="true" />
          {active ? "Live operation" : turn ? "Last operation" : "Operation"}
        </span>
        <span className="cc-operation-state" data-tone={operation.tone} role="status">
          <span className="cc-operation-dot" data-tone={operation.tone} aria-hidden="true" />
          {operation.phase}
        </span>
        {/* Collapsed, the strip has room to name the assistant as well as the
            phase - unless the phase already does, as "Assigned to X" does. */}
        {!open && operation.agent && !namesAgent && (
          <span className="cc-operation-peek">Assigned to {operation.agent}</span>
        )}
        <button
          type="button"
          className="cc-operation-toggle"
          aria-expanded={open}
          aria-controls="cc-operation-body"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />}
          {open ? "Hide detail" : "Show detail"}
        </button>
      </header>

      {open && (
        <div id="cc-operation-body" className="cc-operation-body">
          <div className="cc-operation-request">
            <p className="cc-operation-label">{turn ? "Working on" : "Ready"}</p>
            <p className="cc-operation-prompt">
              {turn ? turn.prompt : "Ask a question to start a live operation."}
            </p>
          </div>

          <dl className="cc-operation-meta">
            <div>
              <dt>Phase</dt>
              <dd>{operation.phase}</dd>
            </div>
            <div>
              <dt>Assigned to</dt>
              <dd>{operation.agent ?? "Not reported yet"}</dd>
            </div>
          </dl>

          <div className="cc-operation-activity">
            <p className="cc-operation-label">Activity</p>
            <ActivityTimeline
              entries={timeline.slice(0, 5)}
              emptyMessage={
                turn
                  ? "The assistant has not recorded a step yet."
                  : "No request is running."
              }
            />
          </div>

          {streamStatus === "reconnecting" && (
            <p className="cc-operation-foot">Reconnecting to the activity stream...</p>
          )}
          {streamStatus === "offline" && (
            <p className="cc-operation-foot" data-tone="failed">
              Activity stream unavailable. The answer will still return to the conversation.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
