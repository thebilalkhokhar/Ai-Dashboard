"use client";

// The living JARVIS orb.
//
// A dependency-free canvas renderer draws a bright pastel sphere: six large
// colour fields ride slowly around the volume while their hues drift, so the
// ball genuinely rotates and changes colour instead of shimmering in place. A
// halftone dot screen sits over the colour (the printed look) and the rim washes
// back out to white. There are no particles, no dialer and no dark core.
//
// It runs continuously while idle, and a state change only adjusts the target
// rotation speed and colour energy - the animation never restarts.
//
// The canvas is capped at 2x DPR and pauses its RAF loop when the tab is hidden.
// Under prefers-reduced-motion a single static frame is drawn instead.

import { useEffect, useRef } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { ORB_CAPTION, type OrbState } from "@/lib/presentation";

const TAU = Math.PI * 2;

// One blob per hue of the reference palette. `orbit`/`tilt` place it on the
// sphere, `speed` decides which way it travels (mixed directions read as a
// rotating surface rather than a sliding wallpaper), `radius` its spread.
const FIELDS: Array<{
  hue: number;
  orbit: number;
  tilt: number;
  radius: number;
  speed: number;
  phase: number;
}> = [
  { hue: 186, orbit: 0.5, tilt: 0.7, radius: 0.96, speed: 1, phase: 0.0 },
  { hue: 209, orbit: 0.6, tilt: 0.5, radius: 0.88, speed: -0.74, phase: 1.1 },
  { hue: 47, orbit: 0.44, tilt: 0.84, radius: 1.0, speed: 0.62, phase: 2.15 },
  { hue: 21, orbit: 0.62, tilt: 0.42, radius: 0.86, speed: -0.92, phase: 3.2 },
  { hue: 323, orbit: 0.38, tilt: 0.92, radius: 0.9, speed: 0.7, phase: 4.3 },
  { hue: 148, orbit: 0.56, tilt: 0.6, radius: 0.82, speed: -0.55, phase: 5.35 },
];

// Screen tone: dot pitch as a fraction of the sphere, and a fixed rotation so
// the grid reads as printed rather than screen-aligned.
const DOT_PITCH = 1 / 44;
const DOT_ROTATION = 0.4;

function stateEnergy(state: OrbState): { speed: number; boost: number; failed: number } {
  switch (state) {
    case "listening":
      return { speed: 0.8, boost: 0.92, failed: 0 };
    case "thinking":
      return { speed: 1.05, boost: 1, failed: 0 };
    case "executing":
      return { speed: 1.8, boost: 1.18, failed: 0 };
    case "responding":
      return { speed: 1.3, boost: 1.05, failed: 0 };
    case "completed":
      return { speed: 0.55, boost: 0.8, failed: 0 };
    case "failed":
      return { speed: 0.35, boost: 0.38, failed: 1 };
    default:
      return { speed: 0.42, boost: 0.82, failed: 0 };
  }
}

function hsla(h: number, s: number, l: number, alpha: number): string {
  return `hsla(${h.toFixed(1)}, ${s.toFixed(1)}%, ${l.toFixed(1)}%, ${alpha.toFixed(3)})`;
}

interface Blob {
  x: number;
  y: number;
  r: number;
  hue: number;
}

// Where each colour field sits at this moment. Riding the fields around the
// centre in mixed directions is what reads as the sphere turning; the shared hue
// drift is what makes it change colour rather than only move.
function fieldBlobs(
  radius: number,
  cx: number,
  cy: number,
  spin: number,
  hueDrift: number,
  time: number,
): Blob[] {
  return FIELDS.map((field) => {
    const angle = spin * field.speed + field.phase;
    return {
      x: cx + Math.cos(angle) * radius * field.orbit,
      y:
        cy +
        Math.sin(angle) * radius * field.orbit * field.tilt +
        Math.sin(spin * 0.55 + field.phase) * radius * 0.06,
      r: radius * field.radius * (1 + Math.sin(time * 0.21 + field.phase) * 0.09),
      hue: (field.hue + hueDrift) % 360,
    };
  });
}

// How much colour covers one point, so the dot screen tracks the paint under it.
function coverage(blobs: Blob[], x: number, y: number): number {
  let total = 0;
  for (const blob of blobs) {
    const d = Math.hypot(x - blob.x, y - blob.y) / blob.r;
    if (d < 1) total += (1 - d) * (1 - d);
  }
  return Math.min(1, total * 0.7);
}

function drawOrb(
  ctx: CanvasRenderingContext2D,
  size: number,
  time: number,
  state: OrbState,
  smooth: { speed: number; boost: number; failed: number },
) {
  if (!size) return;
  const radius = size / 2;
  const cx = radius;
  const cy = radius;
  ctx.clearRect(0, 0, size, size);
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, TAU);
  ctx.clip();

  // Pale base: the reference sphere is lit from within, never a dark volume.
  ctx.fillStyle = "#f6fbff";
  ctx.fillRect(0, 0, size, size);

  const spin = time * (0.1 + smooth.speed * 0.17);
  const hueDrift = time * (9 + smooth.speed * 14);
  const blobs = fieldBlobs(radius, cx, cy, spin, hueDrift, time);

  // Colour fields. Painted over the pale base with normal alpha, so a thin edge
  // stays white and only real overlaps deepen - multiply turned the whole volume
  // muddy because six saturated layers stacked to near black.
  ctx.globalCompositeOperation = "source-over";
  for (const blob of blobs) {
    const gradient = ctx.createRadialGradient(blob.x, blob.y, 0, blob.x, blob.y, blob.r);
    const energy = Math.min(1.15, smooth.boost);
    const sat = 96 * Math.min(1, energy);
    const light = 55 - (energy - 1) * 8;
    gradient.addColorStop(0, hsla(blob.hue, sat, light, 0.95));
    gradient.addColorStop(0.42, hsla(blob.hue, sat, light + 4, 0.66));
    gradient.addColorStop(0.74, hsla(blob.hue, sat, light + 10, 0.24));
    gradient.addColorStop(1, hsla(blob.hue, sat, light + 16, 0));
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
  }

  // Halftone screen. Ink dots grow where more colour covers the surface, and
  // the grid scrolls with the rotation so it belongs to the sphere.
  const pitch = Math.max(4, size * DOT_PITCH);
  const cos = Math.cos(DOT_ROTATION);
  const sin = Math.sin(DOT_ROTATION);
  const shift = (spin * radius * 0.5) % pitch;
  ctx.fillStyle = `rgba(58, 66, 104, ${(0.13 * Math.min(1.1, smooth.boost)).toFixed(3)})`;
  ctx.beginPath();
  for (let gy = -radius; gy <= radius; gy += pitch) {
    for (let gx = -radius; gx <= radius; gx += pitch) {
      const ux = gx + shift;
      const uy = gy - shift * 0.6;
      const x = cx + ux * cos - uy * sin;
      const y = cy + ux * sin + uy * cos;
      if (Math.hypot(x - cx, y - cy) > radius * 0.985) continue;
      const dot = pitch * (0.06 + coverage(blobs, x, y) * 0.17);
      ctx.moveTo(x + dot, y);
      ctx.arc(x, y, dot, 0, TAU);
    }
  }
  ctx.fill();

  // Only the outermost sliver fades to white, so the edge stays soft without
  // draining the colour out of the body.
  ctx.globalCompositeOperation = "source-over";
  const edge = ctx.createRadialGradient(cx, cy, radius * 0.86, cx, cy, radius);
  edge.addColorStop(0, "rgba(255, 255, 255, 0)");
  edge.addColorStop(0.72, "rgba(255, 255, 255, 0.08)");
  edge.addColorStop(0.92, "rgba(255, 255, 255, 0.4)");
  edge.addColorStop(1, "rgba(255, 255, 255, 0.62)");
  ctx.fillStyle = edge;
  ctx.fillRect(0, 0, size, size);

  // A small off-centre highlight keeps some spherical depth.
  ctx.globalCompositeOperation = "screen";
  const bloom = ctx.createRadialGradient(
    cx - radius * 0.3,
    cy - radius * 0.34,
    0,
    cx - radius * 0.3,
    cy - radius * 0.34,
    radius * 0.55,
  );
  bloom.addColorStop(0, `rgba(255, 255, 255, ${(0.22 * smooth.boost).toFixed(3)})`);
  bloom.addColorStop(1, "rgba(255, 255, 255, 0)");
  ctx.fillStyle = bloom;
  ctx.fillRect(0, 0, size, size);

  // A failed run drains the colour and dims the sphere; nothing else changes.
  if (smooth.failed > 0.01) {
    ctx.globalCompositeOperation = "saturation";
    ctx.fillStyle = `rgba(140, 140, 140, ${(0.6 * smooth.failed).toFixed(3)})`;
    ctx.fillRect(0, 0, size, size);
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = `rgba(24, 26, 38, ${(0.22 * smooth.failed).toFixed(3)})`;
    ctx.fillRect(0, 0, size, size);
  }

  ctx.restore();
}

export function JarvisOrb({
  state,
  label,
  className = "",
}: {
  state: OrbState;
  label?: string;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stateRef = useRef(state);
  const reducedMotion = useReducedMotion();
  const drawRef = useRef<(time: number) => void>(() => undefined);

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const canvas: HTMLCanvasElement = element;

    let context: CanvasRenderingContext2D | null = null;
    let size = 0;
    let raf = 0;
    let started = performance.now();
    let hidden = false;
    const smooth = { speed: 0.4, boost: 0.8, failed: 0 };

    function resize() {
      // jsdom reports a zero-size canvas and has no 2d context; skip until the
      // element is actually laid out in a browser.
      const rect = canvas.getBoundingClientRect();
      if (rect.width < 1) return;
      if (!context) context = canvas.getContext("2d");
      if (!context) return;
      size = Math.round(rect.width);
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(size * dpr);
      canvas.height = Math.round(size * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw(performance.now());
    }

    function draw(now: number) {
      if (!context || !size) return;
      const target = stateEnergy(stateRef.current);
      smooth.speed += (target.speed - smooth.speed) * 0.05;
      smooth.boost += (target.boost - smooth.boost) * 0.05;
      smooth.failed += (target.failed - smooth.failed) * 0.05;
      drawOrb(context, size, (now - started) / 1000, stateRef.current, smooth);
    }
    drawRef.current = draw;

    function loop(now: number) {
      if (hidden) return;
      draw(now);
      raf = requestAnimationFrame(loop);
    }

    function onVisibility() {
      hidden = document.visibilityState === "hidden";
      if (!hidden && !reducedMotion) {
        started = performance.now();
        raf = requestAnimationFrame(loop);
      } else {
        cancelAnimationFrame(raf);
      }
    }

    resize();
    document.addEventListener("visibilitychange", onVisibility);
    const observer =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(resize)
        : null;
    observer?.observe(canvas);
    if (!reducedMotion) raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onVisibility);
      observer?.disconnect();
      drawRef.current = () => undefined;
    };
  }, [reducedMotion]);

  // Publish the new state without touching a ref during render. Under reduced
  // motion this also redraws the single static frame; otherwise the running
  // loop picks the new target up smoothly, with no restart.
  useEffect(() => {
    stateRef.current = state;
    if (reducedMotion) drawRef.current(performance.now());
  }, [state, reducedMotion]);

  const caption = label ?? ORB_CAPTION[state];
  return (
    <div
      className={`jo ${className}`.trim()}
      data-testid="jarvis-orb"
      data-state={state}
      role="img"
      aria-label={`AI core: ${caption}`}
    >
      <span className="jo-atmosphere" aria-hidden="true" />
      <canvas ref={canvasRef} className="jo-canvas" aria-hidden="true" />
      <span className="jo-caption">{caption}</span>
    </div>
  );
}
