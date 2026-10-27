"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { fetchHealth } from "@/services/api";
import type { HealthResponse } from "@/types/api";

type HealthState =
  | { status: "checking" }
  | { status: "unavailable"; error: string }
  | { status: "ok"; health: HealthResponse }
  | { status: "degraded"; health: HealthResponse };

const HealthContext = createContext<HealthState | null>(null);

export function SystemHealthProvider({ children }: { children: ReactNode }) {
  const [healthState, setHealthState] = useState<HealthState>({ status: "checking" });

  useEffect(() => {
    let isActive = true;
    let isRequestInFlight = false;

    async function checkHealth() {
      if (isRequestInFlight) return;
      isRequestInFlight = true;

      try {
        const health = await fetchHealth();
        if (isActive) {
          setHealthState({ status: health.status, health });
        }
      } catch (error) {
        if (isActive) {
          setHealthState({
            status: "unavailable",
            error: error instanceof Error ? error.message : "Health request failed.",
          });
        }
      } finally {
        isRequestInFlight = false;
      }
    }

    void checkHealth();
    // Poll only while the tab is actually on screen: a dashboard left
    // open in a background tab must not keep pinging the server forever.
    const intervalId = window.setInterval(() => {
      if (!document.hidden) void checkHealth();
    }, 30_000);

    return () => {
      isActive = false;
      window.clearInterval(intervalId);
    };
  }, []);

  return <HealthContext.Provider value={healthState}>{children}</HealthContext.Provider>;
}

export function SystemHealthBadge() {
  const healthState = useContext(HealthContext);

  const status = healthState?.status ?? "ok";
  const label =
    status === "checking"
      ? "Checking backend..."
      : status === "unavailable"
        ? "Backend unreachable"
        : status === "degraded"
          ? "Backend degraded"
          : "Backend online";
  const indicatorClass =
    status === "unavailable" || status === "degraded"
      ? "bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.5)]"
      : status === "checking"
        ? "bg-amber-300 shadow-[0_0_8px_rgba(245,158,11,0.5)]"
        : "bg-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.5)]";
  const detail =
    !healthState || healthState.status === "checking"
      ? undefined
      : healthState.status === "unavailable"
        ? healthState.error
        : [
            `MongoDB: ${healthState.health.mongo ? "ok" : "unavailable"}`,
            `Database: ${healthState.health.database}`,
            `Scheduler: ${healthState.health.scheduler ? "on" : "off"}`,
          ].join(" · ");

  return (
    <div
      className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-foreground/90 transition-all duration-fast hover:border-border-hover hover:bg-surface-raised cursor-default select-none shadow-sm"
      role="status"
      title={detail}
      aria-label={detail ? `${label}. ${detail}` : label}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${indicatorClass}`} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
