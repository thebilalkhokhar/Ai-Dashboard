"use client";

// The operation flow: the route one request actually took.
//
// Left to right it is the assistant picking the request up, the registered
// agents and real tool lookups that worked on it, and the response being
// written. Every node and every moving particle comes from a persisted task
// event or the live chat stream for THIS task - a link only flows while the
// stage it represents is genuinely active, and an idle stage is drawn as
// waiting rather than busy.
//
// This replaces the large idle orb during execution: the point of the active
// view is the work, not the mascot.

import { useId, type ReactNode } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import type { ExecutionPhase } from "@/lib/task-events";
import type { OperationStage, StageState } from "@/lib/presentation";

const CENTER = 50;
const STEP_SPAN = 34; // percent of the stage between two neighbouring nodes
const MAX_SPAN = 78;  // and never nearer the edges than this

interface Placed extends OperationStage {
  x: number;
  y: number;
  column: "intake" | "middle" | "out";
}

// One column per stage, in the order the request moved through them. The span
// grows with the number of stages, so a two-stage turn reads as a compact route
// rather than two dots stranded at opposite ends of an empty card.
function place(stages: OperationStage[]): Placed[] {
  const total = stages.length;
  if (total === 0) return [];
  const span = Math.min(MAX_SPAN, STEP_SPAN * (total - 1));
  const left = CENTER - span / 2;
  const step = total > 1 ? span / (total - 1) : 0;
  return stages.map((stage, index) => {
    const column = total === 1 ? "middle" : index === 0 ? "intake" : index === total - 1 ? "out" : "middle";
    // a shallow zigzag stops neighbouring labels from crowding one line
    const offset = column === "middle" && total > 3 ? (index % 2 === 0 ? -13 : 13) : 0;
    return { ...stage, column, x: left + index * step, y: CENTER + offset };
  });
}

// A bowed path between two nodes, in the 0..100 viewBox.
function link(from: Placed, to: Placed): string {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const dy = to.y - from.y;
  const dx = to.x - from.x;
  return `M ${from.x} ${from.y} Q ${mx - dy * 0.14} ${my + dx * 0.14} ${to.x} ${to.y}`;
}

function stateWord(state: StageState): string {
  switch (state) {
    case "active":
      return "working";
    case "done":
      return "done";
    // "stopped" would read as a cancellation this system cannot perform
    case "failed":
      return "failed";
    default:
      return "waiting";
  }
}

export function OperationFlow({
  stages,
  execution,
  core,
}: {
  stages: OperationStage[];
  execution: ExecutionPhase;
  // the request's origin: the living core, sized for this view rather than the
  // large idle hero orb, which belongs to the waiting state
  core?: ReactNode;
}) {
  const reducedMotion = useReducedMotion();
  const rawId = useId();
  const uid = rawId.replace(/[^a-zA-Z0-9_-]/g, "");
  const placed = place(stages);
  // a route, not a fan: each stage links to the one that followed it
  const edges = placed.slice(1).map((node, index) => ({ from: placed[index], to: node }));

  return (
    <div
      className="jflow"
      data-execution={execution}
      role="group"
      aria-label="Assistant operation flow"
    >
      <svg viewBox="0 0 100 100" className="jflow-svg" aria-hidden="true">
        <defs>
          <linearGradient id={`${uid}-flow`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#7df0ff" />
            <stop offset="55%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#f0abfc" />
          </linearGradient>
          <filter id={`${uid}-soft`} x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="0.7" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {edges.map((edge, index) => {
          const pathId = `${uid}-edge-${index}`;
          const flowing = edge.to.state === "active";
          const settled = edge.from.state === "done" && edge.to.state !== "pending";
          return (
            <g
              key={pathId}
              className="jflow-edge"
              data-flowing={flowing ? "true" : "false"}
              data-settled={settled ? "true" : "false"}
              filter={`url(#${uid}-soft)`}
            >
              <path id={pathId} d={link(edge.from, edge.to)} className="jflow-line" pathLength={1} />
              {flowing && !reducedMotion && (
                <>
                  <path
                    d={link(edge.from, edge.to)}
                    className="jflow-line jflow-line-flow"
                    stroke={`url(#${uid}-flow)`}
                    pathLength={1}
                  />
                  <circle className="jflow-particle" r="0.7">
                    <animateMotion
                      dur={`${1.8 + (index % 3) * 0.4}s`}
                      repeatCount="indefinite"
                      begin={`${(index % 4) * 0.3}s`}
                    >
                      <mpath href={`#${pathId}`} />
                    </animateMotion>
                  </circle>
                </>
              )}
            </g>
          );
        })}
      </svg>

      {placed.map((stage) => {
        if (stage.column === "intake" && core) {
          return (
            <div
              key={stage.id}
              className="jflow-node jflow-node--core"
              data-state={stage.state}
              data-testid={`flow-node-${stage.id}`}
              style={{ left: `${stage.x}%`, top: `${stage.y}%` }}
            >
              <span className="jflow-core">{core}</span>
              <span className="jflow-text">
                <span className="jflow-label">{stage.label}</span>
                <span className="jflow-state">{stateWord(stage.state)}</span>
              </span>
            </div>
          );
        }
        return (
          <div
            key={stage.id}
            className={`jflow-node jflow-node--${stage.column}`}
            data-state={stage.state}
            data-testid={`flow-node-${stage.id}`}
            style={{ left: `${stage.x}%`, top: `${stage.y}%` }}
          >
            <span className="jflow-dot">
              {stage.state === "done" && <span className="jflow-check" aria-hidden="true" />}
              {stage.state === "failed" && <span className="jflow-cross" aria-hidden="true" />}
              {stage.state === "active" && !reducedMotion && (
                <span className="jflow-pulse" aria-hidden="true" />
              )}
            </span>
            <span className="jflow-text">
              <span className="jflow-label">{stage.label}</span>
              <span className="jflow-state">{stateWord(stage.state)}</span>
            </span>
          </div>
        );
      })}

      <ol className="jflow-summary">
        {stages.map((stage) => (
          <li key={stage.id}>
            {stage.label}: {stateWord(stage.state)}
            {stage.detail ? ` - ${stage.detail}` : ""}
          </li>
        ))}
      </ol>
    </div>
  );
}
