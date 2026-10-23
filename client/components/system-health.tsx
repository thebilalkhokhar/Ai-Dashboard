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

  if (!healthState) {
    throw new Error("SystemHealthBadge must be rendered inside SystemHealthProvider.");
  }

  const label =
    healthState.status === "checking"
      ? "Checking backend..."
      : healthState.status === "unavailable"
        ? "Backend unreachable"
        : healthState.status === "degraded"
          ? "Backend degraded"
          : "Backend online";
  const indicatorClass =
    healthState.status === "unavailable" || healthState.status === "degraded"
      ? "bg-red-400"
      : healthState.status === "checking"
        ? "bg-amber-300"
        : "bg-emerald-400";
  const detail =
    healthState.status === "unavailable"
      ? healthState.error
      : healthState.status === "checking"
        ? undefined
        : [
            `MongoDB: ${healthState.health.mongo ? "ok" : "unavailable"}`,
            `Database: ${healthState.health.database}`,
            `Scheduler: ${healthState.health.scheduler ? "on" : "off"}`,
          ].join(" · ");

  return (
    <div
      className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-muted"
      role="status"
      title={detail}
      aria-label={detail ? `${label}. ${detail}` : label}
    >
      <span className={`h-2 w-2 rounded-full ${indicatorClass}`} aria-hidden="true" />
      {label}
    </div>
  );
}
