"use client";

// The Command Center workspace.
//
// Two views of the same truth, never both at once:
//   * idle  - the JARVIS core inside the registered agent network, waiting.
//   * live  - the operation flow: the route this one request is actually taking,
//             node by node, from the task's own events.
// Right column: the one conversation plus the live operation panel. Execution
// history lives in a drawer, so the workspace stays focused on the current
// request and a finished turn is never shown as the one in flight.

import { useEffect, useMemo, useState } from "react";
import { useExecutionController } from "@/components/command-center/execution-context";
import { isLivePhase, mergeAgentNodes, type ExecutionPhase } from "@/lib/task-events";
import {
  deriveExecutionTree,
  deriveOperation,
  deriveOrbState,
  ORB_CAPTION,
  PHASE_CAPTION,
  taskTitle,
} from "@/lib/presentation";
import { JarvisOrb } from "@/components/command-center/jarvis-orb";
import { NeuralNetwork } from "@/components/command-center/neural-network";
import { OperationFlow } from "@/components/command-center/operation-flow";
import { OperationPanel } from "@/components/command-center/operation-panel";
import { ChatInterface } from "@/components/chat-interface";
import { HistoryButton, HistoryDrawer } from "@/components/command-center/history-drawer";

export function CommandCenter() {
  const execution = useExecutionController();
  const [historyOpen, setHistoryOpen] = useState(false);
  const { turn, lastTurn, execution: phase, isLive, events, agents, agentsError, setWorkspaceOwnsActiveView } = execution;

  // A finished turn used to vanish 650 ms after completion, so returning
  // to this tab after a run showed the idle orb instead of the work that
  // just ran. The last turn stays on screen until a new chat or thread
  // replaces it; a live turn still wins while it runs, and a finished one
  // is never shown as in flight - its nodes are all settled.
  const displayTurn = turn ?? lastTurn;
  const displayExecution: ExecutionPhase = turn
    ? phase
    : lastTurn?.phase === "completed"
      ? "completed"
      : lastTurn?.phase === "failed"
        ? "failed"
        : phase;

  // This workspace is the active execution view while the tab is open, so the
  // full-screen overlay stands down instead of drawing a second copy.
  useEffect(() => {
    setWorkspaceOwnsActiveView(true);
    return () => setWorkspaceOwnsActiveView(false);
  }, [setWorkspaceOwnsActiveView]);

  const hasAgentActivity = events.timeline.some((entry) => entry.type.startsWith("agent."));
  const hasToolActivity = events.timeline.some((entry) => entry.type.startsWith("tool."));

  const nodes = useMemo(
    () => mergeAgentNodes(agents, events.agents),
    [agents, events.agents],
  );

  const tree = useMemo(
    () =>
      deriveExecutionTree({
        execution: displayExecution,
        timeline: events.timeline,
        agents: events.agents,
        details: events.details,
      }),
    [displayExecution, events.timeline, events.agents, events.details],
  );

  const orbState = deriveOrbState({
    execution: displayExecution,
    hasTask: Boolean(displayTurn?.taskId),
    hasAgentActivity,
    hasToolActivity,
  });

  // Only the turn on screen is the current operation. Once it closes, the panel
  // returns to "Ready": a previous turn belongs to the conversation and the
  // history drawer, never to "Working on".
  const operation = deriveOperation({
    turn: displayTurn,
    execution: displayExecution,
    timeline: events.timeline,
    agents: events.agents,
  });

  // The flow stays for the last turn, live or just finished; the large
  // idle orb comes back only when a new chat or thread clears it.
  const showFlow = Boolean(displayTurn);

  return (
    <section aria-labelledby="command-center-title" className="cc-workspace">
      <header className="cc-workspace-head">
        <div>
          <p className="cc-workspace-eyebrow">JARVIS</p>
          <h2 id="command-center-title" className="cc-workspace-title">
            {displayTurn ? taskTitle(displayTurn.prompt) : "Executive assistant"}
          </h2>
        </div>
        <div className="cc-workspace-actions">
          <span className="cc-workspace-status" role="status" data-testid="cc-workspace-status">
            <span
              className="cc-workspace-dot"
              data-state={isLive ? "active" : "idle"}
              aria-hidden="true"
            />
            {events.status === "reconnecting"
              ? "Reconnecting"
              : events.status === "offline"
                ? "Activity offline"
                : isLive
                  ? "Executing"
                  : phase === "failed" || phase === "cancelled"
                    ? "Needs attention"
                    : displayTurn
                      ? operation.phase
                      : "Standing by"}
          </span>
          <HistoryButton onClick={() => setHistoryOpen(true)} />
        </div>
      </header>

      <div className="cc-main">
        <div className="cc-visual jglass" data-view={showFlow ? "flow" : "idle"}>
          {showFlow ? (
            <div className="jstage jstage--flow cc-stage" data-testid="cc-operation-flow">
              <OperationFlow
                tree={tree}
                execution={displayExecution}
                core={<JarvisOrb state={orbState} label={ORB_CAPTION[orbState]} className="jo--core" />}
              />
            </div>
          ) : (
            <div className="jstage jstage--hero cc-stage" data-testid="cc-idle-stage">
              <NeuralNetwork nodes={nodes} active={isLivePhase(phase)} />
              <JarvisOrb state="idle" label={ORB_CAPTION.idle} className="jo--hero" />
            </div>
          )}

          <div className="cc-visual-foot">
            <div className="cc-request">
              <p className="cc-request-label">
                {displayTurn ? "Your request" : "Ready for a request"}
              </p>
              {/* the headline carries the clipped version; the card carries the
                  whole thing, so the two say different amounts, not the same */}
              <p className="cc-request-text">
                {displayTurn ? displayTurn.prompt : "Ask about payments, invoices, quotes, orders or leads."}
              </p>
            </div>
            <div className="cc-visual-meta">
              <span className="cc-phase" data-tone={operation.tone}>
                <span className="cc-phase-dot" data-tone={operation.tone} aria-hidden="true" />
                {displayTurn ? operation.phase : PHASE_CAPTION.idle.phase}
              </span>
              {!displayTurn && (
                <span className="cc-agent">{agents.length} assistants registered</span>
              )}
            </div>
          </div>
        </div>

        <div className="cc-side">
          <OperationPanel
            turn={displayTurn}
            execution={displayExecution}
            timeline={events.timeline}
            agents={events.agents}
            streamStatus={events.status}
          />
          <div className="cc-chat jglass">
            <ChatInterface />
          </div>
        </div>
      </div>

      {agentsError && (
        <p className="cc-workspace-note" role="alert">
          {agentsError}
        </p>
      )}
      {events.status === "error" && events.error && (
        <p className="cc-workspace-note" role="alert">
          {events.error} The answer will still return to the conversation.
        </p>
      )}

      <HistoryDrawer open={historyOpen} onOpenChange={setHistoryOpen} />
    </section>
  );
}
