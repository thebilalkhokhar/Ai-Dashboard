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
import { isLivePhase, mergeAgentNodes } from "@/lib/task-events";
import {
  deriveOperation,
  deriveOrbState,
  deriveStages,
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
  const { turn, execution: phase, isLive, events, agents, agentsError, setWorkspaceOwnsActiveView } = execution;

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

  const stages = useMemo(
    () => deriveStages({ execution: phase, timeline: events.timeline, agents: events.agents }),
    [phase, events.timeline, events.agents],
  );

  const orbState = deriveOrbState({
    execution: phase,
    hasTask: Boolean(turn?.taskId),
    hasAgentActivity,
    hasToolActivity,
  });

  // Only the turn on screen is the current operation. Once it closes, the panel
  // returns to "Ready": a previous turn belongs to the conversation and the
  // history drawer, never to "Working on".
  const operation = deriveOperation({
    turn,
    execution: phase,
    timeline: events.timeline,
    agents: events.agents,
  });

  // The flow is shown for as long as a turn is on screen, including its brief
  // completed state; the large idle orb comes back once nothing is running.
  const showFlow = Boolean(turn);

  return (
    <section aria-labelledby="command-center-title" className="cc-workspace">
      <header className="cc-workspace-head">
        <div>
          <p className="cc-workspace-eyebrow">JARVIS</p>
          <h2 id="command-center-title" className="cc-workspace-title">
            {turn ? taskTitle(turn.prompt) : "Executive assistant"}
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
                stages={stages}
                execution={phase}
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
                {turn ? "Your request" : "Ready for a request"}
              </p>
              {/* the headline carries the clipped version; the card carries the
                  whole thing, so the two say different amounts, not the same */}
              <p className="cc-request-text">
                {turn ? turn.prompt : "Ask about payments, invoices, quotes, orders or leads."}
              </p>
            </div>
            <div className="cc-visual-meta">
              <span className="cc-phase" data-tone={operation.tone}>
                <span className="cc-phase-dot" data-tone={operation.tone} aria-hidden="true" />
                {turn ? operation.phase : PHASE_CAPTION.idle.phase}
              </span>
              {!turn && (
                <span className="cc-agent">{agents.length} assistants registered</span>
              )}
            </div>
          </div>
        </div>

        <div className="cc-side">
          <OperationPanel
            turn={turn}
            execution={phase}
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
