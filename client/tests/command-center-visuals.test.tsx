import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { JarvisOrb } from "@/components/command-center/jarvis-orb";
import { NeuralNetwork } from "@/components/command-center/neural-network";
import type { AgentNodeState } from "@/lib/task-events";

function stubReducedMotion(reduced: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduced && query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
    addListener: () => undefined,
    removeListener: () => undefined,
  }));
}

const nodes: AgentNodeState[] = [
  {
    id: "collections",
    name: "Collections",
    kind: "specialist",
    status: "running",
    activeTools: ["get_invoices"],
    lastEventAt: null,
  },
  {
    id: "chat",
    name: "Chat",
    kind: "assistant",
    status: "completed",
    cached: 2,
    activeTools: [],
    lastEventAt: null,
  },
];

beforeEach(() => stubReducedMotion(false));
afterEach(() => vi.unstubAllGlobals());

describe("JarvisOrb", () => {
  it("labels its state in words, not only in colour or motion", () => {
    render(<JarvisOrb state="executing" />);
    expect(screen.getByRole("img", { name: "AI core: Working on it" })).toBeInTheDocument();
    expect(screen.getByText("Working on it")).toBeInTheDocument();
  });

  it("keeps the state attribute so CSS can shift hue and speed per state", () => {
    const { rerender } = render(<JarvisOrb state="idle" />);
    expect(screen.getByRole("img")).toHaveAttribute("data-state", "idle");
    rerender(<JarvisOrb state="failed" />);
    expect(screen.getByRole("img")).toHaveAttribute("data-state", "failed");
  });
});

describe("NeuralNetwork", () => {
  it("shows friendly names, real status words and the cached marker", () => {
    render(<NeuralNetwork nodes={nodes} active />);

    expect(screen.getByText("Collections Assistant")).toBeInTheDocument();
    expect(screen.getByText("Chat Assistant")).toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
    // the amber marker only appears because the backend reported cached items
    expect(document.querySelector(".jn-cached")).not.toBeNull();
    // no identifiers leak into the network labels
    expect(document.querySelector(".jn-stage")?.textContent).not.toMatch(/collections|chat_/);
  });

  it("animates only the connections the events say are working", () => {
    render(<NeuralNetwork nodes={nodes} active />);
    const groups = Array.from(document.querySelectorAll(".jn-link-group"));
    expect(groups).toHaveLength(2);
    expect(groups[0].getAttribute("data-working")).toBe("true");
    expect(groups[1].getAttribute("data-working")).toBe("false");
    expect(document.querySelectorAll(".jn-link-flow")).toHaveLength(1);
  });

  it("drops travelling particles and pulses under prefers-reduced-motion", () => {
    stubReducedMotion(true);
    render(<NeuralNetwork nodes={nodes} active />);

    // the state is still conveyed - just without motion
    expect(screen.getByText("Collections Assistant")).toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(document.querySelectorAll(".jn-link-flow")).toHaveLength(0);
    expect(document.querySelector(".jn-pulse")).toBeNull();
  });
});

describe("state visuals", () => {
  it("supports every orb state without losing its accessible label", () => {
    const states = ["idle", "listening", "thinking", "executing", "responding", "completed", "failed"] as const;
    const { rerender } = render(<JarvisOrb state="idle" />);
    for (const state of states) {
      rerender(<JarvisOrb state={state} />);
      expect(screen.getByRole("img")).toHaveAttribute("data-state", state);
      expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/AI core:/);
    }
  });

  it("animates several working connections at once and marks failed/cached state", () => {
    const concurrent: AgentNodeState[] = [
      { ...nodes[0], id: "negotiation", status: "running", activeTools: [] },
      { ...nodes[0], id: "collections", status: "running", activeTools: [] },
      { ...nodes[0], id: "lead_followup", status: "failed", activeTools: [] },
      { ...nodes[1], id: "chat", status: "completed", cached: 1, activeTools: [] },
    ];
    render(<NeuralNetwork nodes={concurrent} active />);

    expect(document.querySelectorAll(".jn-link-flow")).toHaveLength(2);
    expect(document.querySelectorAll(".jn-traveler")).toHaveLength(2);
    expect(document.querySelector('.jn-node[data-state="failed"]')).not.toBeNull();
    expect(document.querySelector('.jn-link-group[data-cached="true"]')).not.toBeNull();
  });

  it("stays still when idle: no travelling particles or working links", () => {
    const idle = nodes.map((node) => ({ ...node, status: "idle" as const, activeTools: [] }));
    render(<NeuralNetwork nodes={idle} active={false} />);

    expect(document.querySelectorAll(".jn-link-flow")).toHaveLength(0);
    expect(document.querySelectorAll(".jn-traveler")).toHaveLength(0);
    expect(document.querySelector('.jn-stage')).toHaveAttribute("data-active", "false");
  });
});
