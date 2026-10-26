"use client";

// The neural network around the core.
//
// The SVG draws layered, bowed paths with depth and a soft glow; the labels are
// HTML so they stay crisp at every size. Every agent stays visibly wired to
// the core: the links are always drawn, and a slow, dim ambient pulse travels
// them so the web reads as alive and connected while it waits. That pulse is
// decoration, clearly quieter than work - a link only blazes and speeds up
// when that registered agent is genuinely working, and node colours and
// status words come straight from real event state: working, queued, done,
// unavailable, plus an amber marker when the backend reported reused
// (cached) results. Several paths animate at once when several agents are
// concurrent.

import { useEffect, useId, useRef } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { agentLabelFor, agentStatusWord } from "@/lib/presentation";
import type { AgentNodeState } from "@/lib/task-events";

const RADIUS = 44; // percent of the stage, from the centre
const CENTER = 50;

// Each registered assistant has a short looping clip in /public, so the node is
// the assistant's own face rather than a coloured dot. Keyed by registry id:
// no new agent is implied, and an agent without a clip keeps its dot.
// The two CRM agents have still portraits instead of clips (their videos
// could not be generated here), rendered through the same circle crop, so
// no branch node ever shows an empty circle.
const AGENT_CLIPS: Record<string, string> = {
  master: "/master.mp4",
  negotiation: "/negotiations.mp4",
  collections: "/collections.mp4",
  lead_followup: "/leads.mp4",
  chat: "/chat.mp4",
};

const AGENT_IMAGES: Record<string, string> = {
  crm_engagement: "/crm-engagement.webp",
  crm_deal_watch: "/crm-deal-watch.webp",
};

function nodePosition(index: number, total: number): { x: number; y: number } {
  const angle = (Math.PI * 2 * index) / Math.max(total, 1) - Math.PI / 2;
  const rawX = CENTER + RADIUS * Math.cos(angle);
  const rawY = CENTER + RADIUS * Math.sin(angle);
  return {
    x: rawX,
    // Keep every node, and the label under it, inside the square stage.
    y: Math.min(84, Math.max(10, rawY)),
  };
}

// A gently bowed path from the core to a node, so the web reads as organic
// rather than a spoke diagram. Coordinates are in the 0..100 viewBox.
function linkPath(x: number, y: number, bend = 0.16): string {
  const mx = (CENTER + x) / 2;
  const my = (CENTER + y) / 2;
  const dx = x - CENTER;
  const dy = y - CENTER;
  return `M ${CENTER} ${CENTER} Q ${mx - dy * bend} ${my + dx * bend} ${x} ${y}`;
}

// The midpoint of the bowed link (quadratic Bezier at t=0.5), where a small
// relay node sits: the web reads as a layered network, not bare spokes.
function relayPoint(x: number, y: number, bend = 0.16): { x: number; y: number } {
  const mx = (CENTER + x) / 2;
  const my = (CENTER + y) / 2;
  const qx = mx - (y - CENTER) * bend;
  const qy = my + (x - CENTER) * bend;
  return {
    x: 0.25 * CENTER + 0.5 * qx + 0.25 * x,
    y: 0.25 * CENTER + 0.5 * qy + 0.25 * y,
  };
}

function isWorking(node: AgentNodeState): boolean {
  return node.status === "running" || node.activeTools.length > 0;
}

export function NeuralNetwork({
  nodes,
  active = false,
}: {
  nodes: AgentNodeState[];
  // the web is "live" only while a real task is in flight
  active?: boolean;
}) {
  const reducedMotion = useReducedMotion();
  const rawId = useId();
  const uid = rawId.replace(/[^a-zA-Z0-9_-]/g, "");
  const stageRef = useRef<HTMLDivElement | null>(null);

  // Autoplay is only ever enabled when motion is welcome. If the preference
  // changes while the clips are running, stop them on the next tick and leave a
  // still frame painted, because a playing video is the motion they opted out of.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || !reducedMotion) return;
    for (const clip of Array.from(stage.querySelectorAll("video"))) {
      const video = clip as HTMLVideoElement;
      video.pause();
      if (!video.currentTime) video.currentTime = 0.1;
    }
  }, [reducedMotion]);

  return (
    <div
      ref={stageRef}
      className="jn-stage"
      data-active={active ? "true" : "false"}
      role="group"
      aria-label="Agent network"
    >
      <svg viewBox="0 0 100 100" className="jn-svg" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <radialGradient id={`${uid}-core`}>
            <stop offset="0%" stopColor="rgba(125,240,255,0.42)" />
            <stop offset="55%" stopColor="rgba(139,92,246,0.16)" />
            <stop offset="100%" stopColor="rgba(139,92,246,0)" />
          </radialGradient>
          <linearGradient id={`${uid}-flow`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#7df0ff" />
            <stop offset="45%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#f0abfc" />
          </linearGradient>
          <filter id={`${uid}-glow`} x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="0.9" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* atmosphere and orbital scaffolding, never agent activity */}
        <circle cx={CENTER} cy={CENTER} r="30" className="jn-halo" fill={`url(#${uid}-core)`} />
        <circle cx={CENTER} cy={CENTER} r="22" className="jn-orbit" />
        <circle cx={CENTER} cy={CENTER} r="46" className="jn-orbit jn-orbit--outer" />
        <path
          d="M 14 66 C 26 84, 72 88, 88 62"
          className="jn-ambient"
          fill="none"
          pathLength={1}
        />
        <path
          d="M 12 36 C 28 12, 72 12, 89 40"
          className="jn-ambient jn-ambient--reverse"
          fill="none"
          pathLength={1}
        />

        {nodes.map((node, index) => {
          const { x, y } = nodePosition(index, nodes.length);
          const working = isWorking(node);
          const cached = typeof node.cached === "number" && node.cached > 0;
          const state = working ? "running" : node.status;
          const pathId = `${uid}-link-${index}`;
          const duration = 2.4 + (index % 3) * 0.55;

          return (
            <g
              key={node.id}
              className="jn-link-group"
              data-working={working ? "true" : "false"}
              data-state={state}
              data-cached={cached ? "true" : "false"}
              filter={`url(#${uid}-glow)`}
            >
              <path
                d={linkPath(x, y, 0.21)}
                className="jn-link jn-link-echo"
                fill="none"
                pathLength={1}
              />
              <path
                id={pathId}
                d={linkPath(x, y)}
                className="jn-link jn-link-primary"
                fill="none"
                pathLength={1}
              />
              {!reducedMotion && (
                <>
                  <path
                    d={linkPath(x, y)}
                    className={`jn-link jn-link-flow${working ? "" : " jn-link-flow--ambient"}`}
                    fill="none"
                    pathLength={1}
                    stroke={`url(#${uid}-flow)`}
                  />
                  <circle
                    className={`jn-traveler${working ? "" : " jn-traveler--ambient"}`}
                    r={working ? 0.65 : 0.5}
                  >
                    <animateMotion
                      dur={`${working ? duration : duration * 2.6}s`}
                      repeatCount="indefinite"
                      begin={`${(index % 4) * 0.28}s`}
                    >
                      <mpath href={`#${pathId}`} />
                    </animateMotion>
                  </circle>
                  {!working && (
                    <circle className="jn-traveler jn-traveler--ambient" r={0.42}>
                      <animateMotion
                        dur={`${duration * 2.6}s`}
                        repeatCount="indefinite"
                        begin={`${-(duration * 1.3 + (index % 3) * 0.7)}s`}
                      >
                        <mpath href={`#${pathId}`} />
                      </animateMotion>
                    </circle>
                  )}
                  {(() => {
                    const relay = relayPoint(x, y);
                    return (
                      <circle
                        cx={relay.x}
                        cy={relay.y}
                        r={0.55}
                        className="jn-relay"
                        style={{ animationDelay: `${(index % 5) * 0.6}s` }}
                      />
                    );
                  })()}
                </>
              )}
            </g>
          );
        })}
      </svg>

      {nodes.map((node, index) => {
        const { x, y } = nodePosition(index, nodes.length);
        const working = isWorking(node);
        const cached = typeof node.cached === "number" && node.cached > 0;
        const master = node.kind === "orchestrator";
        const clip = AGENT_CLIPS[node.id];
        const image = AGENT_IMAGES[node.id];
        // Only the lowest nodes wear their label above the circle; an avatar is
        // tall enough that labelling the top node from above leaves the stage.
        const placement = y > 68 ? "above" : "below";
        return (
          <div
            key={node.id}
            className={`jn-node${master ? " jn-node--master" : ""}`}
            data-state={node.status}
            data-working={working ? "true" : "false"}
            data-placement={placement}
            data-clip={clip || image ? "true" : "false"}
            style={{ left: `${x}%`, top: `${y}%` }}
          >
            <span className="jn-dot" style={{ animationDelay: `${(index % 5) * 0.7}s` }}>
              {clip && (
                <video
                  className="jn-clip"
                  src={clip}
                  aria-hidden="true"
                  muted
                  loop
                  playsInline
                  autoPlay={!reducedMotion}
                  preload="metadata"
                  tabIndex={-1}
                />
              )}
              {!clip && image && (
                <img className="jn-clip" src={image} alt="" aria-hidden="true" />
              )}
              {node.status === "completed" && <span className="jn-check" aria-hidden="true" />}
              {node.status === "failed" && <span className="jn-cross" aria-hidden="true" />}
              {cached && <span className="jn-cached" aria-hidden="true" />}
              {working && !reducedMotion && <span className="jn-pulse" aria-hidden="true" />}
            </span>
            <span className="jn-text">
              <span className="jn-name">{agentLabelFor(node.id)}</span>
              <span className="jn-status">{agentStatusWord(node.status)}</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
