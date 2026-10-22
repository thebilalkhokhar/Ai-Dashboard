"use client";

// The execution overlay.
//
// It opens for the turn the shared controller is holding and makes the work the
// centrepiece: the operation flow - assistant, real tool lookups, synthesis -
// rather than the large idle orb, which belongs to the waiting state. Its node
// and particle states come from this task's persisted events, so nothing here
// can look busy because an animation happens to be running.
//
// Escape hides this view without touching the request; there is no cancel
// endpoint, so closing is never presented as stopping. A failure stays visible
// with a clear explanation and a retry action instead of vanishing.

import { useEffect, useMemo, useRef } from "react";
import { RotateCcw, X } from "lucide-react";
import { deriveExecutionTree, deriveOperation, taskTitle } from "@/lib/presentation";
import { isLivePhase } from "@/lib/task-events";
import { OperationFlow } from "@/components/command-center/operation-flow";
import { ActivityTimeline } from "@/components/command-center/activity-timeline";
import { useExecutionController } from "@/components/command-center/execution-context";

export function ExecutionOverlay() {
  const { turn, execution, events, dismiss, retry, workspaceOwnsActiveView } = useExecutionController();
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!turn) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      // Safe to close: the chat keeps the request and renders the answer.
      if (event.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [turn, dismiss]);

  const hasAgentActivity = events.timeline.some((entry) => entry.type.startsWith("agent."));
  const hasToolActivity = events.timeline.some((entry) => entry.type.startsWith("tool."));

  const tree = useMemo(
    () =>
      deriveExecutionTree({
        execution,
        timeline: events.timeline,
        agents: events.agents,
        details: events.details,
      }),
    [execution, events.timeline, events.agents, events.details],
  );

  const operation = useMemo(
    () => deriveOperation({ turn, execution, timeline: events.timeline, agents: events.agents }),
    [turn, execution, events.timeline, events.agents],
  );

  if (!turn || workspaceOwnsActiveView) return null;

  const failed = execution === "failed" || execution === "cancelled";
  const finished = execution === "completed";
  const active = isLivePhase(execution);
  const live = hasAgentActivity || hasToolActivity;

  return (
    <div
      className="joverlay"
      data-closing={finished ? "true" : "false"}
      data-phase={execution}
      role="dialog"
      aria-modal="true"
      aria-label={`Assistant working: ${taskTitle(turn.prompt)}`}
    >
      <button
        ref={closeRef}
        type="button"
        onClick={dismiss}
        className="joverlay-close"
        aria-label="Hide this view (Escape). The request keeps running and the answer will still appear in the chat."
      >
        <X size={13} aria-hidden="true" />
        Hide
      </button>

      <div className="joverlay-inner">
        <div className="joverlay-stage">
          <div className="jstage jstage--flow" data-testid="overlay-operation-flow">
            <OperationFlow tree={tree} execution={execution} />
          </div>
          <p className="joverlay-stage-note" role="status">
            {active
              ? live
                ? "Each lit node is a step the assistant recorded for this request."
                : "Waiting for the assistant to record its first step."
              : finished
                ? "Every recorded step is complete."
                : "This request did not complete."}
          </p>
        </div>

        <div className="joverlay-panel jglass">
          <div className="joverlay-panel-head">
            <p className="joverlay-eyebrow">
              {failed ? "Could not complete" : finished ? "Complete" : "In progress"}
            </p>
            <h2 className="joverlay-title">{taskTitle(turn.prompt)}</h2>
            <p className="joverlay-request" title={turn.prompt}>
              {turn.prompt}
            </p>
          </div>

          <div className="joverlay-operation" role="status" aria-live="polite">
            <span className="cc-phase" data-tone={operation.tone}>
              <span className="cc-phase-dot" data-tone={operation.tone} aria-hidden="true" />
              {operation.phase}
            </span>
            <span className="joverlay-agent">
              {operation.agent
                ? `Assigned to ${operation.agent}`
                : "Waiting for the assigned assistant"}
            </span>
          </div>

          <div className="joverlay-activity">
            <ActivityTimeline
              entries={events.timeline}
              details={events.details}
              showDiagnostics={failed}
              emptyMessage={
                turn.taskId
                  ? "The assistant has started; activity will appear as it happens."
                  : "Waiting for the assistant to pick up your request..."
              }
            />
          </div>

          {failed && (
            <div className="joverlay-recovery" role="alert">
              <p>{turn.message || "That request did not complete."}</p>
              <button type="button" onClick={retry}>
                <RotateCcw size={13} aria-hidden="true" />
                Try again
              </button>
            </div>
          )}

          {!failed && events.status === "reconnecting" && (
            <p className="joverlay-note">Reconnecting to the activity stream...</p>
          )}
          {!failed && (events.status === "offline" || events.status === "error") && (
            <p className="joverlay-note">
              Detailed activity is unavailable. The answer will still return to the conversation.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
