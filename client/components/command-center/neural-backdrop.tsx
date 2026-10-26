"use client";

// The neural formation behind the operation tree.
//
// A fixed, deterministic web of layered neurons and synapse fibres covers the
// whole stage - not a handful of decorative dots. The geometry is generated
// once from a seeded PRNG, so the server and the browser always draw the same
// network and it never reshuffles between renders. A few somas read brighter
// than the rest, and dim signals drift along selected fibres; while a request
// is actually running the web brightens and extra, faster pulses travel it,
// including a few warm amber flashes borrowed from real nervous tissue.
//
// This is atmosphere only: it never changes a stage's state, never implies
// progress, and it is completely still under prefers-reduced-motion.

import { useId, useMemo } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";

const WIDTH = 1000;
const HEIGHT = 620;

interface Neuron {
  id: string;
  x: number;
  y: number;
  layer: number;
  r: number;
  hub: boolean;
}

interface Fibre {
  id: string;
  d: string;
  width: number;
  tone: "cool" | "violet" | "warm";
  opacity: number;
}

interface Signal {
  id: string;
  fibre: string;
  duration: number;
  delay: number;
  amber: boolean;
  radius: number;
}

// Small deterministic PRNG (mulberry32) so the formation is identical on every
// render and on the server.
function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function buildFormation() {
  const random = seeded(0x5eed1a7);
  const layers = 9;
  const counts = [5, 7, 9, 10, 11, 10, 9, 7, 5];
  const neurons: Neuron[] = [];

  for (let layer = 0; layer < layers; layer += 1) {
    const count = counts[layer];
    const x = 34 + (layer / (layers - 1)) * (WIDTH - 68);
    for (let index = 0; index < count; index += 1) {
      // A little column wobble keeps the layers from looking like a ladder.
      const columnJitter = (random() - 0.5) * 34;
      const spread = (HEIGHT - 130) / Math.max(count - 1, 1);
      const y = 65 + index * spread + (random() - 0.5) * spread * 0.72;
      const hub = random() > 0.8;
      neurons.push({
        id: `n-${layer}-${index}`,
        x: Math.max(18, Math.min(WIDTH - 18, x + columnJitter)),
        y: Math.max(28, Math.min(HEIGHT - 28, y)),
        layer,
        r: hub ? 3.4 + random() * 1.9 : 1.7 + random() * 1.5,
        hub,
      });
    }
  }

  const byLayer: Neuron[][] = Array.from({ length: layers }, () => []);
  for (const neuron of neurons) byLayer[neuron.layer].push(neuron);

  const fibres: Fibre[] = [];
  const seen = new Set<string>();
  const addFibre = (from: Neuron, to: Neuron, tone: Fibre["tone"], width: number) => {
    const key = `${from.id}>${to.id}`;
    if (seen.has(key)) return;
    seen.add(key);
    // Fibres bow out to one side so the network reads as tissue, not a grid.
    const mx = (from.x + to.x) / 2;
    const my = (from.y + to.y) / 2;
    const bow = (random() - 0.5) * Math.max(26, Math.abs(to.y - from.y) * 0.34);
    const lift = (random() - 0.5) * 28;
    fibres.push({
      id: `f-${fibres.length}`,
      d: `M ${from.x.toFixed(1)} ${from.y.toFixed(1)} Q ${(mx + lift).toFixed(1)} ${(my + bow).toFixed(1)} ${to.x.toFixed(1)} ${to.y.toFixed(1)}`,
      width,
      tone,
      opacity: 0.32 + random() * 0.42,
    });
  };

  for (let layer = 0; layer < layers - 1; layer += 1) {
    const next = byLayer[layer + 1];
    const tone: Fibre["tone"] = layer % 4 === 3 ? "violet" : "cool";
    for (const neuron of byLayer[layer]) {
      // Feed the two nearest neurons in the next layer, then occasionally a
      // farther one, which is what gives the web its long association fibres.
      const ranked = [...next].sort(
        (a, b) => Math.abs(a.y - neuron.y) - Math.abs(b.y - neuron.y),
      );
      addFibre(neuron, ranked[0], tone, 0.55 + random() * 0.5);
      if (ranked[1]) addFibre(neuron, ranked[1], tone, 0.45 + random() * 0.45);
      if (random() > 0.72 && ranked.length > 2) {
        const far = ranked[2 + Math.floor(random() * (ranked.length - 2))];
        addFibre(neuron, far, "violet", 0.4 + random() * 0.4);
      }
      if (random() > 0.9 && layer + 2 < layers) {
        const skipLayer = byLayer[layer + 2];
        const target = skipLayer[Math.floor(random() * skipLayer.length)];
        addFibre(neuron, target, "cool", 0.35 + random() * 0.3);
      }
    }
    // A few short lateral synapses inside the layer itself.
    for (let index = 0; index < byLayer[layer].length - 1; index += 1) {
      if (random() > 0.78) {
        addFibre(byLayer[layer][index], byLayer[layer][index + 1], "violet", 0.32);
      }
    }
  }

  const signalCandidates = fibres.filter((_, index) => index % 4 === 1).slice(0, 18);
  const signals: Signal[] = signalCandidates.map((fibre, index) => ({
    id: `s-${index}`,
    fibre: fibre.id,
    duration: 6.5 + (index % 5) * 1.4,
    delay: -(index * 0.93),
    amber: index % 5 === 2,
    radius: index % 6 === 0 ? 2.6 : 1.9,
  }));

  return { neurons, fibres, signals };
}

const FORMATION = buildFormation();

export function NeuralBackdrop({ active = false }: { active?: boolean }) {
  const reducedMotion = useReducedMotion();
  const rawId = useId();
  const uid = rawId.replace(/[^a-zA-Z0-9_-]/g, "");

  // Extra, faster pulses only while real work is in flight.
  const liveSignals = useMemo(() => {
    if (!active) return [] as Signal[];
    return FORMATION.fibres
      .filter((_, index) => index % 9 === 4)
      .slice(0, 10)
      .map((fibre, index) => ({
        id: `live-${index}`,
        fibre: fibre.id,
        duration: 2.6 + (index % 3) * 0.6,
        delay: -(index * 0.41),
        amber: index % 4 === 1,
        radius: 2.2,
      }));
  }, [active]);

  return (
    <div
      className="jtree-neural"
      data-live={active ? "true" : "false"}
      aria-hidden="true"
    >
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="xMidYMid slice"
        className="jtree-mesh jtree-neural-svg"
      >
        <defs>
          <radialGradient id={`${uid}-soma`}>
            <stop offset="0%" stopColor="rgba(224, 248, 255, 0.95)" />
            <stop offset="45%" stopColor="rgba(125, 211, 252, 0.72)" />
            <stop offset="100%" stopColor="rgba(59, 130, 246, 0)" />
          </radialGradient>
          <radialGradient id={`${uid}-soma-warm`}>
            <stop offset="0%" stopColor="rgba(255, 236, 210, 0.95)" />
            <stop offset="48%" stopColor="rgba(251, 146, 60, 0.55)" />
            <stop offset="100%" stopColor="rgba(251, 146, 60, 0)" />
          </radialGradient>
          <filter id={`${uid}-signal-glow`} x="-120%" y="-120%" width="340%" height="340%">
            <feGaussianBlur stdDeviation="3.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <g className="jtree-neural-fibres">
          {FORMATION.fibres.map((fibre) => (
            <path
              key={fibre.id}
              id={`${uid}-${fibre.id}`}
              d={fibre.d}
              className={`jtree-mesh-line jtree-neural-fibre jtree-neural-fibre--${fibre.tone}`}
              vectorEffect="non-scaling-stroke"
              strokeWidth={fibre.width}
              strokeOpacity={fibre.opacity}
            />
          ))}
        </g>

        {/* dendrite twigs around the larger somas */}
        <g className="jtree-neural-dendrites">
          {FORMATION.neurons
            .filter((neuron) => neuron.hub)
            .map((neuron, index) => (
              <g key={`d-${neuron.id}`} stroke={`rgba(148, 210, 255, ${0.14 + (index % 3) * 0.04})`} fill="none">
                {[0, 1, 2, 3].map((twig) => {
                  const angle = (Math.PI * 2 * twig) / 4 + index * 0.7;
                  const length = neuron.r * (3.6 + (twig % 2) * 1.6);
                  const x2 = neuron.x + Math.cos(angle) * length;
                  const y2 = neuron.y + Math.sin(angle) * length;
                  const cx = neuron.x + Math.cos(angle + 0.5) * length * 0.62;
                  const cy = neuron.y + Math.sin(angle + 0.5) * length * 0.62;
                  return (
                    <path
                      key={twig}
                      d={`M ${neuron.x} ${neuron.y} Q ${cx} ${cy} ${x2} ${y2}`}
                      vectorEffect="non-scaling-stroke"
                      strokeWidth="0.7"
                    />
                  );
                })}
              </g>
            ))}
        </g>

        <g className="jtree-neural-neurons">
          {FORMATION.neurons.map((neuron) => (
            <g key={neuron.id} className={neuron.hub ? "jtree-neural-soma is-hub" : "jtree-neural-soma"}>
              <circle
                cx={neuron.x}
                cy={neuron.y}
                r={neuron.r * (neuron.hub ? 4.2 : 2.9)}
                fill={`url(#${uid}-${neuron.hub && neuron.layer % 5 === 2 ? "soma-warm" : "soma"})`}
                opacity={neuron.hub ? 0.26 : 0.12}
              />
              <circle
                cx={neuron.x}
                cy={neuron.y}
                r={neuron.r}
                className="jtree-mesh-node jtree-neural-core"
              />
            </g>
          ))}
        </g>

        {!reducedMotion && (
          <g className="jtree-neural-signals" filter={`url(#${uid}-signal-glow)`}>
            {[...FORMATION.signals, ...liveSignals].map((signal) => (
              <circle
                key={signal.id}
                r={signal.radius}
                className={`jtree-mesh-pulse jtree-neural-signal${signal.amber ? " is-amber" : ""}${signal.id.startsWith("live") ? " is-live" : ""}`}
              >
                <animateMotion
                  dur={`${signal.duration}s`}
                  begin={`${signal.delay}s`}
                  repeatCount="indefinite"
                >
                  <mpath href={`#${uid}-${signal.fibre}`} />
                </animateMotion>
              </circle>
            ))}
          </g>
        )}
      </svg>
    </div>
  );
}
