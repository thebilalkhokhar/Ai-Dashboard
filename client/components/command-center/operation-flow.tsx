"use client";

// The operation tree: the route one request actually took, drawn LARGE.
//
// The root is the assistant that picked the request up (its face, or the
// living core in the workspace). When one of its lookups belongs to a
// registered specialist's territory, the tree grows a full branch node -
// that specialist's own avatar - and hangs the real lookup under it.
// Under each lookup hang the data stages it genuinely performed: the
// filtering stage with the backend's real matched/returned counts, and
// the aggregation marker when the tool totalled values in Python. Then
// the answer-validation stage (the numeric guard's real verdict) and
// the response node close the tree.
//
// Every node, edge and count comes from this task's persisted events:
// a branch exists only because a tool of its territory really ran, a
// link flows only while the stage it feeds is active, counts are the
// backend's own numbers, and nothing is timed or interpolated. Nodes
// are buttons: selecting one shows its recorded detail under the stage.

import { useId, useState, type ReactNode } from "react";
import { ShieldCheck } from "lucide-react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { NeuralBackdrop } from "@/components/command-center/neural-backdrop";
import { isLivePhase, type ExecutionPhase } from "@/lib/task-events";
import type {
  ExecutionTree,
  OperationStage,
  StageState,
  TreeToolStage,
} from "@/lib/presentation";

const AGENT_CLIPS: Record<string, string> = {
  master: "/master.mp4",
  negotiation: "/negotiations.mp4",
  collections: "/collections.mp4",
  lead_followup: "/leads.mp4",
  chat: "/chat.mp4",
};

// Still portraits for the two CRM agents, same circle crop as the clips,
// so a branch node never renders an empty circle.
const AGENT_IMAGES: Record<string, string> = {
  crm_engagement: "/crm-engagement.webp",
  crm_deal_watch: "/crm-deal-watch.webp",
};

// Tree columns, in percent of the stage. The gaps are wide enough for the
// widest card in each column at every stage size (the cards themselves scale
// with the stage via container query units) and the two right-most columns
// keep the response inside the stage instead of clipping it.
const ROOT_X = 8;
const BRANCH_X = 27;
const LEAF_X = 46;
const PROC_X = 65;
const VALID_X = 83;
const OUT_X = 95;
const CENTER_Y = 50;
// Minimum vertical distance between two specialist cards, in percent of the
// stage. Below this the name card of one would sit on the other's avatar.
const BRANCH_GAP = 30;

// Every stage circle carries a real picture, so no circle ever reads
// empty: the agent branches already have portraits, and the tool-type
// stages get their own emblem per kind (search / review / running work /
// totals). Done and failed keep their state as a corner badge on top of
// the picture - the picture itself never disappears.
const STAGE_IMAGES = {
  search: "/stage-search.webp",
  review: "/stage-review.webp",
  analysis: "/stage-analysis.webp",
  totals: "/stage-totals.webp",
  generic: "/stage-generic.webp",
} as const;
const STAGE_RESPONSE_IMAGE = "/stage-response.webp";

function stageImage(label: string): string {
  if (/search/i.test(label)) return STAGE_IMAGES.search;
  if (/review/i.test(label)) return STAGE_IMAGES.review;
  if (/running|analysis/i.test(label)) return STAGE_IMAGES.analysis;
  if (/total|aggregation/i.test(label)) return STAGE_IMAGES.totals;
  return STAGE_IMAGES.generic;
}

function stateWord(state: StageState): string {
  switch (state) {
    case "active":
      return "working";
    case "done":
      return "done";
    case "failed":
      return "failed";
    default:
      return "waiting";
  }
}

function formatDuration(ms: number | null): string | null {
  if (ms === null) return null;
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

interface Placed {
  id: string;
  x: number;
  y: number;
}

interface Edge {
  key: string;
  from: Placed;
  to: Placed;
  flowing: boolean;
  settled: boolean;
  contract?: boolean;
}

interface DetailInfo {
  title: string;
  state: StageState;
  lines: string[];
}

function link(from: Placed, to: Placed): string {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const dy = to.y - from.y;
  const dx = to.x - from.x;
  return `M ${from.x} ${from.y} Q ${mx - dy * 0.12} ${my + dx * 0.12} ${to.x} ${to.y}`;
}

export function OperationFlow({
  tree,
  execution,
  core,
}: {
  tree: ExecutionTree;
  execution: ExecutionPhase;
  core?: ReactNode;
}) {
  const reducedMotion = useReducedMotion();
  const rawId = useId();
  const uid = rawId.replace(/[^a-zA-Z0-9_-]/g, "");
  const [selected, setSelected] = useState<string | null>(null);

  // ------------------------------------------------------------ layout
  // One row slot per lookup / agent stage; branches sit at the centre of
  // their lookups' slots, so the tree grows taller with the real work.
  const hasBranches = tree.branches.length > 0;
  const leafX = hasBranches ? LEAF_X : BRANCH_X;
  const allTools = [...tree.branches.flatMap((b) => b.tools), ...tree.directTools];
  const slots: { kind: "tool" | "agent" | "more"; ref: unknown }[] = [];
  for (const branch of tree.branches) for (const tool of branch.tools) slots.push({ kind: "tool", ref: tool });
  for (const tool of tree.directTools) slots.push({ kind: "tool", ref: tool });
  for (const stage of tree.agentStages) slots.push({ kind: "agent", ref: stage });
  if (tree.moreStage) slots.push({ kind: "more", ref: tree.moreStage });

  // Rows stay inside 13%..87% of the stage so the first and last card never
  // collide with the column labels above or the detail strip below.
  const span = slots.length <= 1 ? 0 : Math.min(74, (slots.length - 1) * 30);
  const slotY = (index: number) =>
    slots.length <= 1 ? CENTER_Y : CENTER_Y - span / 2 + (span / (slots.length - 1)) * index;

  const rootP: Placed = { id: "root", x: ROOT_X, y: CENTER_Y };
  const outP: Placed = { id: "out", x: OUT_X, y: CENTER_Y };
  const validP: Placed | null = tree.validation ? { id: "validation", x: VALID_X, y: CENTER_Y } : null;

  const toolPos = new Map<string, Placed>();
  const branchPos = new Map<string, Placed>();
  const stagePos = new Map<string, Placed>();
  slots.forEach((slot, index) => {
    const y = slotY(index);
    if (slot.kind === "tool") {
      const tool = slot.ref as TreeToolStage;
      toolPos.set(tool.stage.id, { id: tool.stage.id, x: leafX, y });
    } else {
      const stage = slot.ref as OperationStage;
      stagePos.set(stage.id, { id: stage.id, x: leafX, y });
    }
  });
  // Branch cards are taller than lookup cards, and two branches can easily
  // share a row when their lookups interleave. Place them at the middle of
  // their lookups, then push them apart in work order so no two cards touch;
  // the whole stack is shifted back inside the stage if it runs past the base.
  const branchLanes = tree.branches
    .map((branch) => {
      const ys = branch.tools.map((tool) => toolPos.get(tool.stage.id)!.y);
      return {
        branch,
        y: ys.reduce((a, b) => a + b, 0) / Math.max(1, ys.length),
      };
    })
    .sort((a, b) => a.y - b.y);
  let cursor = 13;
  for (const lane of branchLanes) {
    lane.y = Math.max(lane.y, cursor);
    cursor = lane.y + BRANCH_GAP;
  }
  const stackBottom = cursor - BRANCH_GAP;
  const bottomRoom = 87;
  if (stackBottom > bottomRoom) {
    const shift = stackBottom - bottomRoom;
    for (const lane of branchLanes) lane.y -= shift;
  }
  for (const lane of branchLanes) {
    lane.y = Math.max(13, Math.min(bottomRoom, lane.y));
    branchPos.set(lane.branch.agentId, {
      id: `branch-${lane.branch.agentId}`,
      x: BRANCH_X,
      y: lane.y,
    });
  }

  // Both data stages of one lookup read as one processing cell on the same
  // row, not as two unrelated pills floating around it.
  const procPos = new Map<string, Placed>();
  for (const tool of allTools) {
    if (!tool.filter && !tool.aggregation) continue;
    const base = toolPos.get(tool.stage.id)!;
    procPos.set(tool.tool, { id: `proc-${tool.tool}`, x: PROC_X, y: base.y });
  }

  // ------------------------------------------------------------- edges
  const edges: Edge[] = [];
  const terminal: Placed[] = [];
  const pushEdge = (key: string, from: Placed, to: Placed, toState: StageState, fromState: StageState, contract = false) => {
    edges.push({
      key,
      from,
      to,
      flowing: toState === "active",
      settled: fromState === "done" && toState === "done",
      contract,
    });
  };
  for (const branch of tree.branches) {
    const bp = branchPos.get(branch.agentId)!;
    pushEdge(`root-${branch.agentId}`, rootP, bp, branch.state, tree.root.state);
    for (const tool of branch.tools) {
      const tp = toolPos.get(tool.stage.id)!;
      pushEdge(`${branch.agentId}-${tool.stage.id}`, bp, tp, tool.stage.state, branch.state);
    }
  }
  for (const tool of tree.directTools) {
    pushEdge(`root-${tool.stage.id}`, rootP, toolPos.get(tool.stage.id)!, tool.stage.state, tree.root.state);
  }
  for (const stage of tree.agentStages) {
    pushEdge(`root-${stage.id}`, rootP, stagePos.get(stage.id)!, stage.state, tree.root.state);
  }
  if (tree.moreStage) {
    pushEdge(`root-${tree.moreStage.id}`, rootP, stagePos.get(tree.moreStage.id)!, tree.moreStage.state, tree.root.state);
  }
  for (const tool of allTools) {
    const tp = toolPos.get(tool.stage.id)!;
    const pp = procPos.get(tool.tool);
    if (!pp) {
      terminal.push(tp);
      continue;
    }
    pushEdge(`${tool.stage.id}-processing`, tp, pp, "done", tool.stage.state, Boolean(tool.filter));
    terminal.push(pp);
  }
  for (const stage of tree.agentStages) terminal.push(stagePos.get(stage.id)!);
  if (tree.moreStage) terminal.push(stagePos.get(tree.moreStage.id)!);
  if (terminal.length === 0) terminal.push(rootP);

  if (validP && tree.validation) {
    for (const node of terminal) {
      pushEdge(`${node.id}-validation`, node, validP, tree.validation.state, "done");
    }
    pushEdge("validation-out", validP, outP, tree.response.state, tree.validation.state);
  } else {
    for (const node of terminal) {
      pushEdge(`${node.id}-out`, node, outP, tree.response.state, "done");
    }
  }

  // ------------------------------------------------------------ detail
  function toolDetail(tool: TreeToolStage): DetailInfo {
    const lines = [tool.stage.detail];
    const duration = formatDuration(tool.durationMs);
    if (duration) lines.push(`Lookup took ${duration}.`);
    if (tool.counts) {
      lines.push(
        `${tool.counts.returned} of ${tool.counts.total} matched records returned` +
          (tool.counts.remaining !== null ? `, ${tool.counts.remaining} still to fetch.` : "."),
      );
    }
    if (tool.pagination) {
      lines.push(
        `Fetched across ${tool.pagination.pages} ${tool.pagination.pages === 1 ? "page" : "pages"}.`,
      );
    }
    return { title: tool.stage.label, state: tool.stage.state, lines };
  }

  const details = new Map<string, DetailInfo>();
  details.set(tree.root.id, {
    title: tree.root.label,
    state: tree.root.state,
    lines: [tree.root.detail, "Picked up the request and routed the work."],
  });
  for (const branch of tree.branches) {
    details.set(`branch-${branch.agentId}`, {
      title: branch.label,
      state: branch.state,
      lines: [
        "Routed by the Chat Assistant - this territory's lookups run under it.",
        ...branch.tools.map((tool) => `${tool.stage.label}: ${stateWord(tool.stage.state)}`),
      ],
    });
    for (const tool of branch.tools) details.set(tool.stage.id, toolDetail(tool));
  }
  for (const tool of tree.directTools) details.set(tool.stage.id, toolDetail(tool));
  for (const stage of tree.agentStages) {
    details.set(stage.id, { title: stage.label, state: stage.state, lines: [stage.detail] });
  }
  if (tree.moreStage) {
    details.set(tree.moreStage.id, {
      title: tree.moreStage.label,
      state: tree.moreStage.state,
      lines: [tree.moreStage.detail],
    });
  }
  for (const tool of allTools) {
    if (tool.filter) {
      details.set(`filter-${tool.tool}`, {
        title: "Filtering",
        state: "done",
        lines: [
          `${tool.filter.matched} records matched the request.`,
          `${tool.filter.returned} were returned in this page.`,
          tool.filter.remaining !== null
            ? `${tool.filter.remaining} more can still be fetched.`
            : "The walk over the matched set is complete.",
        ],
      });
    }
    if (tool.aggregation) {
      details.set(`aggregation-${tool.tool}`, {
        title: "Aggregation",
        state: "done",
        lines: [
          `The backend totalled ${tool.aggregation.aggregate} in Python.`,
          "The value itself travels with the answer's evidence, never as an animation.",
        ],
      });
    }
  }
  if (tree.validation) {
    details.set("validation", {
      title: "Answer validation",
      state: tree.validation.state,
      lines: [
        tree.validation.outcome === "corrected"
          ? "The first draft cited a figure the records did not support; it was regenerated and then verified."
          : tree.validation.outcome === "replaced"
            ? "The draft could not be verified, so it was replaced with a summary built only from the recorded results."
            : tree.validation.outcome === "passed"
              ? "Every figure in the answer was checked against this turn's recorded tool results."
              : "Checking every figure in the answer against the recorded tool results.",
      ],
    });
  }
  details.set(tree.response.id, {
    title: tree.response.label,
    state: tree.response.state,
    lines: [tree.response.detail],
  });

  const selectedDetail = selected ? details.get(selected) : undefined;
  const hasProcessing = allTools.some((tool) => tool.filter || tool.aggregation);

  // ------------------------------------------------------------- nodes
  return (
    <div
      className="jtree"
      data-execution={execution}
      data-live={isLivePhase(execution) ? "true" : "false"}
      data-density={slots.length > 3 ? "dense" : "normal"}
      role="group"
      aria-label="Assistant operation tree"
    >
      <div className="jtree-columns" aria-hidden="true">
        <span className="jtree-column-label" style={{ left: `${ROOT_X}%` }}>Assistant</span>
        {hasBranches && (
          <span className="jtree-column-label" style={{ left: `${BRANCH_X}%` }}>Specialist</span>
        )}
        <span className="jtree-column-label" style={{ left: `${leafX}%` }}>Lookups</span>
        {hasProcessing && (
          <span className="jtree-column-label" style={{ left: `${PROC_X}%` }}>Processing</span>
        )}
        {tree.validation && (
          <span
            className="jtree-column-label jtree-column-label--validation"
            style={{ left: `${VALID_X}%` }}
          >
            Validation
          </span>
        )}
        <span
          className="jtree-column-label jtree-column-label--response"
          style={{ left: `${OUT_X}%` }}
        >
          Response
        </span>
      </div>
      <div className="jtree-stage">
        <div className="jtree-grid" aria-hidden="true" />
        <div className="jtree-glow" aria-hidden="true" />
        <NeuralBackdrop active={isLivePhase(execution)} />
        <div className="jtree-sweep" aria-hidden="true" />
        <svg viewBox="0 0 100 100" className="jtree-svg" aria-hidden="true" preserveAspectRatio="none">
          <defs>
            <linearGradient id={`${uid}-flow`} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#7df0ff" />
              <stop offset="55%" stopColor="#a78bfa" />
              <stop offset="100%" stopColor="#f0abfc" />
            </linearGradient>
          </defs>
          {edges.map((edge, index) => {
            const pathId = `${uid}-edge-${index}`;
            return (
              <g key={edge.key} className="jtree-edge" data-flowing={edge.flowing ? "true" : "false"} data-settled={edge.settled ? "true" : "false"}>
                <path id={pathId} d={link(edge.from, edge.to)} className="jtree-line" pathLength={1} vectorEffect="non-scaling-stroke" />
                {edge.contract && !reducedMotion && isLivePhase(execution) && (
                  <>
                    {[0, 1, 2].map((dot) => (
                      <circle key={dot} className="jtree-contract-dot" r="1">
                        <animateMotion dur="1.9s" repeatCount="indefinite" begin={`${-dot * 0.63}s`}>
                          <mpath href={`#${pathId}`} />
                        </animateMotion>
                        <animate attributeName="r" values="1.05;0.38" dur="1.9s" repeatCount="indefinite" begin={`${-dot * 0.63}s`} />
                      </circle>
                    ))}
                  </>
                )}
                {edge.flowing && !reducedMotion && (
                  <>
                    <path d={link(edge.from, edge.to)} className="jtree-line jtree-line-flow" stroke={`url(#${uid}-flow)`} pathLength={1} vectorEffect="non-scaling-stroke" />
                    {[0, 1].map((dot) => (
                      <circle key={dot} className="jtree-particle" r="0.65">
                        <animateMotion dur={`${1.7 + (index % 3) * 0.35 + dot * 0.5}s`} repeatCount="indefinite" begin={`${(index % 4) * 0.3 + dot * 0.8}s`}>
                          <mpath href={`#${pathId}`} />
                        </animateMotion>
                      </circle>
                    ))}
                  </>
                )}
              </g>
            );
          })}
        </svg>

        {/* root */}
        <button
          type="button"
          className="jtree-node jtree-node--root"
          data-state={tree.root.state}
          data-testid={`flow-node-${tree.root.id}`}
          style={{ left: `${ROOT_X}%`, top: `${CENTER_Y}%` }}
          onClick={() => setSelected(tree.root.id)}
          aria-label={`${tree.root.label}: ${stateWord(tree.root.state)}`}
        >
          {core ? (
            <span className="jtree-core">{core}</span>
          ) : (
            <span className="jtree-avatar jtree-avatar--lg">
              {/* A branch/root clip is motion for work in progress: once the
              node settles, it holds a still frame instead of looping. The
              key remounts on the flip, because flipping autoPlay alone
              never pauses a video that is already playing. */}
              <video key={tree.root.state === "active" && !reducedMotion ? "live" : "still"} className="jtree-clip" src={AGENT_CLIPS.chat} aria-hidden="true" muted loop={tree.root.state === "active" && !reducedMotion} playsInline autoPlay={tree.root.state === "active" && !reducedMotion} preload="metadata" tabIndex={-1} />
              {tree.root.state === "done" && <span className="jtree-badge jtree-badge--done" aria-hidden="true" />}
              {tree.root.state === "failed" && <span className="jtree-badge jtree-badge--failed" aria-hidden="true" />}
              {tree.root.state === "active" && !reducedMotion && <span className="jtree-ring" aria-hidden="true" />}
            </span>
          )}
          <span className="jtree-text">
            <span className="jtree-label">{tree.root.label}</span>
            <span className="jtree-state">{stateWord(tree.root.state)}</span>
          </span>
        </button>

        {/* branches */}
        {tree.branches.map((branch) => {
          const pos = branchPos.get(branch.agentId)!;
          const clip = AGENT_CLIPS[branch.agentId];
          const image = AGENT_IMAGES[branch.agentId];
          return (
            <button
              key={branch.agentId}
              type="button"
              className="jtree-node jtree-node--branch"
              data-state={branch.state}
              data-testid={`flow-branch-${branch.agentId}`}
              style={{ left: `${pos.x}%`, top: `${pos.y}%`, animationDelay: "60ms" }}
              onClick={() => setSelected(`branch-${branch.agentId}`)}
              aria-label={`${branch.label}: ${stateWord(branch.state)}`}
            >
              <span className="jtree-avatar jtree-avatar--md">
                {clip && (
                  <video key={branch.state === "active" && !reducedMotion ? "live" : "still"} className="jtree-clip" src={clip} aria-hidden="true" muted loop={branch.state === "active" && !reducedMotion} playsInline autoPlay={branch.state === "active" && !reducedMotion} preload="metadata" tabIndex={-1} />
                )}
                {!clip && image && (
                  <img className="jtree-clip" src={image} alt="" aria-hidden="true" />
                )}
                {branch.state === "done" && <span className="jtree-badge jtree-badge--done" aria-hidden="true" />}
                {branch.state === "failed" && <span className="jtree-badge jtree-badge--failed" aria-hidden="true" />}
                {branch.state === "active" && !reducedMotion && <span className="jtree-ring" aria-hidden="true" />}
              </span>
              <span className="jtree-text">
                <span className="jtree-label">{branch.label}</span>
                <span className="jtree-state">{stateWord(branch.state)}</span>
                <span className="jtree-sub">routed by Chat Assistant</span>
              </span>
            </button>
          );
        })}

        {/* agent stages (pipeline) and the earlier-lookups stage */}
        {tree.agentStages.map((stage) => {
          const pos = stagePos.get(stage.id)!;
          return (
            <button
              key={stage.id}
              type="button"
              className="jtree-node jtree-node--leaf"
              data-state={stage.state}
              data-testid={`flow-node-${stage.id}`}
              style={{ left: `${pos.x}%`, top: `${pos.y}%`, animationDelay: "90ms" }}
              onClick={() => setSelected(stage.id)}
              aria-label={`${stage.label}: ${stateWord(stage.state)}`}
            >
              <span className="jtree-dot">
                <img className="jtree-dot-img" src={stageImage(stage.label)} alt="" aria-hidden="true" />
                {stage.state === "done" && <span className="jtree-dot-badge jtree-dot-badge--done" aria-hidden="true">✓</span>}
                {stage.state === "failed" && <span className="jtree-dot-badge jtree-dot-badge--failed" aria-hidden="true">✕</span>}
                {stage.state === "active" && !reducedMotion && (<><span className="jtree-pulse" aria-hidden="true" /><span className="jtree-ring" aria-hidden="true" /></>)}
              </span>
              <span className="jtree-text">
                <span className="jtree-label">{stage.label}</span>
                <span className="jtree-state">{stateWord(stage.state)}</span>
              </span>
            </button>
          );
        })}
        {tree.moreStage && (
          <button
            type="button"
            className="jtree-node jtree-node--leaf"
            data-state={tree.moreStage.state}
            data-testid={`flow-node-${tree.moreStage.id}`}
            style={{ left: `${stagePos.get(tree.moreStage.id)!.x}%`, top: `${stagePos.get(tree.moreStage.id)!.y}%` }}
            onClick={() => setSelected(tree.moreStage!.id)}
            aria-label={`${tree.moreStage.label}: ${stateWord(tree.moreStage.state)}`}
          >
            <span className="jtree-dot">
              <img className="jtree-dot-img" src={STAGE_IMAGES.search} alt="" aria-hidden="true" />
              {tree.moreStage.state === "done" && <span className="jtree-dot-badge jtree-dot-badge--done" aria-hidden="true">✓</span>}
              {tree.moreStage.state === "failed" && <span className="jtree-dot-badge jtree-dot-badge--failed" aria-hidden="true">✕</span>}
            </span>
            <span className="jtree-text">
              <span className="jtree-label">{tree.moreStage.label}</span>
              <span className="jtree-state">{stateWord(tree.moreStage.state)}</span>
            </span>
          </button>
        )}

        {/* tool leaves */}
        {allTools.map((tool, index) => {
          const pos = toolPos.get(tool.stage.id)!;
          const duration = formatDuration(tool.durationMs);
          return (
            <button
              key={tool.stage.id}
              type="button"
              className="jtree-node jtree-node--leaf"
              data-state={tool.stage.state}
              data-testid={`flow-node-${tool.stage.id}`}
              style={{ left: `${pos.x}%`, top: `${pos.y}%`, animationDelay: `${90 + (index % 5) * 50}ms` }}
              onClick={() => setSelected(tool.stage.id)}
              aria-label={`${tool.stage.label}: ${stateWord(tool.stage.state)}`}
            >
              <span className="jtree-dot">
                <img className="jtree-dot-img" src={stageImage(tool.stage.label)} alt="" aria-hidden="true" />
                {tool.stage.state === "done" && <span className="jtree-dot-badge jtree-dot-badge--done" aria-hidden="true">✓</span>}
                {tool.stage.state === "failed" && <span className="jtree-dot-badge jtree-dot-badge--failed" aria-hidden="true">✕</span>}
                {tool.stage.state === "active" && !reducedMotion && (<><span className="jtree-pulse" aria-hidden="true" /><span className="jtree-ring" aria-hidden="true" /></>)}
              </span>
              <span className="jtree-text">
                <span className="jtree-label">{tool.stage.label}</span>
                <span className="jtree-state">
                  {stateWord(tool.stage.state)}{duration ? ` · ${duration}` : ""}
                </span>
                {tool.pagination && (
                  <span className="jtree-sub">
                    {tool.pagination.pages} {tool.pagination.pages === 1 ? "page" : "pages"} fetched
                  </span>
                )}
                {tool.counts && (
                  <span className="jtree-counts">
                    {tool.counts.returned.toLocaleString("en-US")} of {tool.counts.total.toLocaleString("en-US")} matched
                    {tool.counts.remaining !== null && tool.counts.remaining > 0
                      ? ` · ${tool.counts.remaining.toLocaleString("en-US")} to fetch`
                      : ""}
                  </span>
                )}
              </span>
            </button>
          );
        })}

        {/* processing: both data stages of one lookup form a single cell */}
        {allTools.flatMap((tool) => {
          const pos = procPos.get(tool.tool);
          if (!pos) return [];
          return [
            <div
              key={`proc-${tool.tool}`}
              className="jtree-proc"
              style={{ left: `${pos.x}%`, top: `${pos.y}%` }}
            >
              {tool.filter && (
                <button
                  type="button"
                  className="jtree-pill jtree-pill--filter"
                  data-state="done"
                  data-testid={`flow-stage-filter-${tool.tool}`}
                  onClick={() => setSelected(`filter-${tool.tool}`)}
                  aria-label={`Filtered ${tool.filter.matched} to ${tool.filter.returned} records`}
                >
                  Filtered · {tool.filter.matched} → {tool.filter.returned}
                </button>
              )}
              {tool.aggregation && (
                <button
                  type="button"
                  className="jtree-pill jtree-pill--agg"
                  data-state="done"
                  data-testid={`flow-stage-aggregation-${tool.tool}`}
                  onClick={() => setSelected(`aggregation-${tool.tool}`)}
                  aria-label="Totals calculated by the backend"
                >
                  <img className="jtree-pill-img" src={STAGE_IMAGES.totals} alt="" aria-hidden="true" />
                  Totals calculated
                </button>
              )}
            </div>,
          ];
        })}

        {/* validation */}
        {tree.validation && (
          <button
            type="button"
            className="jtree-node jtree-node--validation"
            data-state={tree.validation.state}
            data-testid="flow-node-validation"
            style={{ left: `${VALID_X}%`, top: `${CENTER_Y}%`, animationDelay: "120ms" }}
            onClick={() => setSelected("validation")}
            aria-label={`Answer validation: ${stateWord(tree.validation.state)}`}
          >
            <span className="jtree-shield" data-state={tree.validation.state}>
              <ShieldCheck size={22} aria-hidden="true" />
              {tree.validation.state === "active" && !reducedMotion && <span className="jtree-ring" aria-hidden="true" />}
            </span>
            <span className="jtree-text">
              <span className="jtree-label">Answer validation</span>
              <span className="jtree-state">{stateWord(tree.validation.state)}</span>
              {tree.validation.outcome && <span className="jtree-sub">{tree.validation.outcome}</span>}
            </span>
          </button>
        )}

        {/* response */}
        <button
          type="button"
          className="jtree-node jtree-node--out"
          data-state={tree.response.state}
          data-testid={`flow-node-${tree.response.id}`}
          style={{ left: `${OUT_X}%`, top: `${CENTER_Y}%`, animationDelay: "150ms" }}
          onClick={() => setSelected(tree.response.id)}
          aria-label={`${tree.response.label}: ${stateWord(tree.response.state)}`}
        >
          <span className="jtree-orb-mini" data-state={tree.response.state}>
            <img className="jtree-orb-img" src={STAGE_RESPONSE_IMAGE} alt="" aria-hidden="true" />
            {tree.response.state === "active" && !reducedMotion && <span className="jtree-ring" aria-hidden="true" />}
          </span>
          <span className="jtree-text">
            <span className="jtree-label">{tree.response.label}</span>
            <span className="jtree-state">{stateWord(tree.response.state)}</span>
          </span>
        </button>
      </div>

      <div className="jtree-detail" role="status">
        {selectedDetail ? (
          <>
            <span className="jtree-detail-title">
              {selectedDetail.title}
              <span className="jtree-detail-state" data-state={selectedDetail.state}>
                {stateWord(selectedDetail.state)}
              </span>
            </span>
            {selectedDetail.lines.map((line) => (
              <span key={line} className="jtree-detail-line">{line}</span>
            ))}
          </>
        ) : (
          <span className="jtree-detail-line">
            Select any stage to inspect it - every node here is a step this request actually recorded.
          </span>
        )}
      </div>

      <ol className="jtree-summary">
        <li>{tree.root.label}: {stateWord(tree.root.state)}</li>
        {tree.branches.map((branch) => (
          <li key={branch.agentId}>{branch.label}: {stateWord(branch.state)}</li>
        ))}
        {allTools.map((tool) => (
          <li key={tool.stage.id}>{tool.stage.label}: {stateWord(tool.stage.state)}</li>
        ))}
        {tree.validation && <li>Answer validation: {stateWord(tree.validation.state)}</li>}
        <li>{tree.response.label}: {stateWord(tree.response.state)}</li>
      </ol>
    </div>
  );
}
