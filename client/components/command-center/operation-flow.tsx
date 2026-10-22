"use client";

// The operation tree: the route one request actually took, drawn as a
// growing tree instead of a flat line.
//
// The root is the assistant that picked the request up. When one of its
// lookups belongs to a registered specialist's territory (unpaid invoices
// to Collections, open quotes to Negotiation, stuck leads to Lead
// Follow-up, business totals to Master AI), the tree grows a branch for
// that specialist and hangs the real lookup under it - the hand-off the
// reader asked to see. Plain record lookups stay as leaves on the root.
// The last node is the response being written from the verified results.
//
// Every node, branch and particle comes from a persisted task event or
// the live chat stream for THIS task: a branch exists only because a
// tool of its territory really ran, a link only flows while the stage it
// feeds is genuinely active, and a node is only drawn finished when its
// own completion event arrived. The tree grows because events arrive,
// never because a timer fired.

import { useId, type ReactNode } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import type { ExecutionPhase } from "@/lib/task-events";
import {
  agentLabel,
  toolDomainAgent,
  type OperationStage,
  type StageState,
} from "@/lib/presentation";

// Tree columns, in percent of the stage.
const ROOT_X = 9;
const BRANCH_X = 38;
const LEAF_X = 65;
const OUT_X = 93;
const CENTER_Y = 50;

interface PlacedStage extends OperationStage {
  x: number;
  y: number;
  column: "intake" | "middle" | "out";
  order: number;
}

interface Branch {
  agentId: string;
  label: string;
  state: StageState;
  x: number;
  y: number;
  order: number;
  leaves: PlacedStage[];
}

interface Edge {
  key: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
  flowing: boolean;
  settled: boolean;
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

// Worst state wins while anything is still moving: a branch is working
// while any of its lookups is, failed only when a lookup failed and none
// is still running, done when every lookup finished.
function branchState(leaves: OperationStage[]): StageState {
  if (leaves.some((leaf) => leaf.state === "active")) return "active";
  if (leaves.some((leaf) => leaf.state === "failed")) return "failed";
  if (leaves.length > 0 && leaves.every((leaf) => leaf.state === "done")) return "done";
  return "pending";
}

// Lay the stages out as a tree. The middle stages become rows: one row
// per specialist branch (its lookups fan out to the leaf column), one row
// per direct lookup, one row per agent stage a batch run reported. Rows
// spread around the centre, so the tree grows taller - and settles back -
// with the amount of real work this request needed.
function layout(stages: OperationStage[]) {
  const intake = stages[0];
  const out = stages[stages.length - 1];
  const middle = stages.slice(1, -1);

  const agentStages = middle.filter((stage) => stage.id.startsWith("agent-"));
  const toolStages = middle.filter(
    (stage) => stage.id.startsWith("tool-") && stage.id !== "tool-more",
  );
  const moreStage = middle.find((stage) => stage.id === "tool-more");

  // group the lookups by the territory they belong to, in first-use order
  const domainOrder: string[] = [];
  const byDomain = new Map<string, OperationStage[]>();
  for (const stage of toolStages) {
    const domain = toolDomainAgent(stage.id.slice("tool-".length));
    if (!byDomain.has(domain)) {
      byDomain.set(domain, []);
      domainOrder.push(domain);
    }
    byDomain.get(domain)!.push(stage);
  }

  type Row =
    | { kind: "branch"; branch: Branch }
    | { kind: "leaf"; stage: OperationStage }
    | { kind: "agent"; stage: OperationStage }
    | { kind: "more"; stage: OperationStage };

  const rows: Row[] = [];
  for (const domain of domainOrder) {
    const leaves = byDomain.get(domain)!;
    if (domain === "chat") {
      for (const leaf of leaves) rows.push({ kind: "leaf", stage: leaf });
    } else {
      rows.push({
        kind: "branch",
        branch: {
          agentId: domain,
          label: agentLabel(domain),
          state: branchState(leaves),
          x: BRANCH_X,
          y: CENTER_Y,
          order: 0,
          leaves: leaves.map((leaf) => ({ ...leaf, x: LEAF_X, y: CENTER_Y, column: "middle" as const, order: 0 })),
        },
      });
    }
  }
  for (const stage of agentStages) rows.push({ kind: "agent", stage });
  if (moreStage) rows.push({ kind: "more", stage: moreStage });

  // spread the rows around the centre; a branch's leaves fan around it
  const span = Math.min(72, Math.max(0, (rows.length - 1) * 23));
  const startY = CENTER_Y - span / 2;
  const step = rows.length > 1 ? span / (rows.length - 1) : 0;
  let order = 1;

  const placedIntake: PlacedStage = { ...intake, x: ROOT_X, y: CENTER_Y, column: "intake", order: 0 };
  const placedOut: PlacedStage = { ...out, x: OUT_X, y: CENTER_Y, column: "out", order: 99 };
  const branches: Branch[] = [];
  const rowNodes: PlacedStage[] = [];

  rows.forEach((row, index) => {
    const y = rows.length === 1 ? CENTER_Y : startY + index * step;
    if (row.kind === "branch") {
      const branch = row.branch;
      branch.y = y;
      branch.order = order++;
      const leafSpan = Math.min(30, Math.max(0, (branch.leaves.length - 1) * 13));
      branch.leaves = branch.leaves.map((leaf, leafIndex) => ({
        ...leaf,
        y:
          branch.leaves.length === 1
            ? y
            : y - leafSpan / 2 + (leafSpan / Math.max(1, branch.leaves.length - 1)) * leafIndex,
        order: order++,
      }));
      branches.push(branch);
    } else {
      rowNodes.push({ ...row.stage, x: BRANCH_X, y, column: "middle", order: order++ });
    }
  });

  const edges: Edge[] = [];
  const root = { x: ROOT_X, y: CENTER_Y };
  const outPoint = { x: OUT_X, y: CENTER_Y };
  const outFlowing = placedOut.state === "active";
  const outSettled = placedOut.state === "done";

  for (const branch of branches) {
    edges.push({
      key: `root-${branch.agentId}`,
      from: root,
      to: { x: branch.x, y: branch.y },
      flowing: branch.state === "active",
      settled: branch.state === "done",
    });
    for (const leaf of branch.leaves) {
      edges.push({
        key: `${branch.agentId}-${leaf.id}`,
        from: { x: branch.x, y: branch.y },
        to: { x: leaf.x, y: leaf.y },
        flowing: leaf.state === "active",
        settled: leaf.state === "done" && branch.state !== "active",
      });
      edges.push({
        key: `${leaf.id}-out`,
        from: { x: leaf.x, y: leaf.y },
        to: outPoint,
        flowing: outFlowing && leaf.state === "done",
        settled: outSettled,
      });
    }
  }
  for (const node of rowNodes) {
    edges.push({
      key: `root-${node.id}`,
      from: root,
      to: { x: node.x, y: node.y },
      flowing: node.state === "active",
      settled: node.state === "done",
    });
    edges.push({
      key: `${node.id}-out`,
      from: { x: node.x, y: node.y },
      to: outPoint,
      flowing: outFlowing && node.state === "done",
      settled: outSettled,
    });
  }
  if (branches.length === 0 && rowNodes.length === 0) {
    edges.push({
      key: "root-out",
      from: root,
      to: outPoint,
      flowing: outFlowing,
      settled: outSettled,
    });
  }

  const leaves = branches.flatMap((branch) => branch.leaves);
  return { placedIntake, placedOut, branches, rowNodes, leaves, edges };
}

// A bowed path between two points, in the 0..100 viewBox.
function link(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const dy = to.y - from.y;
  const dx = to.x - from.x;
  return `M ${from.x} ${from.y} Q ${mx - dy * 0.14} ${my + dx * 0.14} ${to.x} ${to.y}`;
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
  const { placedIntake, placedOut, branches, rowNodes, leaves, edges } = layout(stages);

  function renderStage(stage: PlacedStage, leaf = false) {
    if (stage.column === "intake" && core) {
      return (
        <div
          key={stage.id}
          className="jflow-node jflow-node--core"
          data-state={stage.state}
          data-testid={`flow-node-${stage.id}`}
          style={{ left: `${stage.x}%`, top: `${stage.y}%`, animationDelay: "0ms" }}
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
        className={`jflow-node jflow-node--${stage.column}${leaf ? " jflow-node--leaf" : ""}`}
        data-state={stage.state}
        data-testid={`flow-node-${stage.id}`}
        style={{
          left: `${stage.x}%`,
          top: `${stage.y}%`,
          animationDelay: `${Math.min(stage.order, 8) * 70}ms`,
        }}
      >
        <span className="jflow-dot">
          {stage.state === "done" && <span className="jflow-check" aria-hidden="true" />}
          {stage.state === "failed" && <span className="jflow-cross" aria-hidden="true" />}
          {stage.state === "active" && !reducedMotion && (
            <>
              <span className="jflow-pulse" aria-hidden="true" />
              <span className="jflow-ring" aria-hidden="true" />
            </>
          )}
        </span>
        <span className="jflow-text">
          <span className="jflow-label">{stage.label}</span>
          <span className="jflow-state">{stateWord(stage.state)}</span>
        </span>
      </div>
    );
  }

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
          return (
            <g
              key={edge.key}
              className="jflow-edge"
              data-flowing={edge.flowing ? "true" : "false"}
              data-settled={edge.settled ? "true" : "false"}
              filter={`url(#${uid}-soft)`}
            >
              <path id={pathId} d={link(edge.from, edge.to)} className="jflow-line" pathLength={1} />
              {edge.flowing && !reducedMotion && (
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

      {renderStage(placedIntake)}

      {branches.map((branch) => (
        <div
          key={branch.agentId}
          className="jflow-node jflow-branch"
          data-state={branch.state}
          data-testid={`flow-branch-${branch.agentId}`}
          style={{
            left: `${branch.x}%`,
            top: `${branch.y}%`,
            animationDelay: `${Math.min(branch.order, 8) * 70}ms`,
          }}
        >
          <span className="jflow-dot jflow-branch-dot">
            {branch.state === "done" && <span className="jflow-check" aria-hidden="true" />}
            {branch.state === "failed" && <span className="jflow-cross" aria-hidden="true" />}
            {branch.state === "active" && !reducedMotion && (
              <>
                <span className="jflow-pulse" aria-hidden="true" />
                <span className="jflow-ring" aria-hidden="true" />
              </>
            )}
          </span>
          <span className="jflow-text">
            <span className="jflow-label">{branch.label}</span>
            <span className="jflow-state">{stateWord(branch.state)}</span>
            <span className="jflow-branch-tag">routed by Chat Assistant</span>
          </span>
        </div>
      ))}

      {rowNodes.map((stage) => renderStage(stage))}
      {leaves.map((stage) => renderStage(stage, true))}
      {renderStage(placedOut)}

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
