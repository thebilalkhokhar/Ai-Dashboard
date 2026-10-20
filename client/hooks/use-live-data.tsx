"use client";

// One EventSource for the whole app, following GET /api/live:
//   event: snapshot  -> dashboard metrics
//   event: briefing  -> the latest finished agent run
//   event: error     -> a backend-reported problem
// plus ": heartbeat" comments, which EventSource ignores.
//
// The first paint comes from GET /api/metrics + GET /api/briefing/latest so the
// dashboard is populated even before the stream opens.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { API_BASE_URL, fetchLatestBriefing, fetchMetrics } from "@/services/api";
import type {
  AgentRunBriefing,
  DashboardSnapshot,
  LatestBriefingResponse,
  LiveConnectionStatus,
  LiveErrorPayload,
} from "@/types/api";

const LIVE_URL = `${API_BASE_URL}/api/live`;

interface LiveDataValue {
  snapshot: DashboardSnapshot | null;
  briefing: AgentRunBriefing | null;
  status: LiveConnectionStatus;
  error: string;
  lastEventAt: string | null;
  refresh: () => Promise<void>;
  setBriefing: (briefing: AgentRunBriefing | null) => void;
}

const LiveDataContext = createContext<LiveDataValue | null>(null);

function parseEvent<T>(event: MessageEvent): T | null {
  try {
    return JSON.parse(String(event.data)) as T;
  } catch {
    return null;
  }
}

function toBriefing(response: LatestBriefingResponse): AgentRunBriefing | null {
  return response.run_id === null ? null : response;
}

function messageOf(reason: unknown): string {
  return reason instanceof Error ? reason.message : "";
}

export function LiveDataProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [briefing, setBriefing] = useState<AgentRunBriefing | null>(null);
  const [status, setStatus] = useState<LiveConnectionStatus>("connecting");
  const [error, setError] = useState("");
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const mountedRef = useRef(true);

  // Manual refresh (buttons). The mount effect below does its own first load.
  const refresh = useCallback(async () => {
    const results = await Promise.allSettled([
      fetchMetrics(),
      fetchLatestBriefing(),
    ]);

    if (!mountedRef.current) return;

    const [metricsResult, briefingResult] = results;
    if (metricsResult.status === "fulfilled") {
      setSnapshot(metricsResult.value);
      setLastEventAt(new Date().toISOString());
    }
    if (briefingResult.status === "fulfilled") {
      setBriefing(toBriefing(briefingResult.value));
    }

    const failure =
      metricsResult.status === "rejected"
        ? metricsResult.reason
        : briefingResult.status === "rejected"
          ? briefingResult.reason
          : null;

    setError(messageOf(failure));
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    let isActive = true;

    Promise.allSettled([fetchMetrics(), fetchLatestBriefing()]).then(
      ([metricsResult, briefingResult]) => {
        if (!isActive) return;

        if (metricsResult.status === "fulfilled") {
          setSnapshot(metricsResult.value);
          setLastEventAt(new Date().toISOString());
        }
        if (briefingResult.status === "fulfilled") {
          setBriefing(toBriefing(briefingResult.value));
        }

        const failure =
          metricsResult.status === "rejected"
            ? metricsResult.reason
            : briefingResult.status === "rejected"
              ? briefingResult.reason
              : null;
        setError(messageOf(failure));
      },
    );

    if (typeof EventSource === "undefined") {
      queueMicrotask(() => {
        if (!isActive) return;
        setStatus("offline");
        setError("This browser does not support Server-Sent Events.");
      });

      return () => {
        isActive = false;
        mountedRef.current = false;
      };
    }

    const source = new EventSource(LIVE_URL);

    source.onopen = () => {
      if (!isActive) return;
      setStatus("live");
      setError("");
    };

    source.addEventListener("snapshot", (event) => {
      if (!isActive) return;
      const data = parseEvent<DashboardSnapshot>(event as MessageEvent);
      if (!data) return;
      setSnapshot(data);
      setStatus("live");
      setError("");
      setLastEventAt(new Date().toISOString());
    });

    source.addEventListener("briefing", (event) => {
      if (!isActive) return;
      const data = parseEvent<AgentRunBriefing | null>(event as MessageEvent);
      setBriefing(data ?? null);
      setError("");
      setLastEventAt(new Date().toISOString());
    });

    // Server-sent `event: error` arrives as a MessageEvent. The browser's own
    // connection-error Event has no payload and is handled below.
    source.addEventListener("error", (event) => {
      if (!isActive) return;
      if (!(event instanceof MessageEvent) || !event.data) return;
      const data = parseEvent<LiveErrorPayload>(event);
      setError(data?.error || data?.message || "The live stream reported an error.");
    });

    source.onerror = () => {
      if (!isActive) return;
      // EventSource reconnects on its own; onopen flips this back to live.
      setStatus("reconnecting");
    };

    return () => {
      isActive = false;
      mountedRef.current = false;
      source.close();
    };
  }, []);

  return (
    <LiveDataContext.Provider
      value={{
        snapshot,
        briefing,
        status,
        error,
        lastEventAt,
        refresh,
        setBriefing,
      }}
    >
      {children}
    </LiveDataContext.Provider>
  );
}

export function useLiveData(): LiveDataValue {
  const value = useContext(LiveDataContext);
  if (!value) {
    throw new Error("useLiveData must be used inside LiveDataProvider.");
  }
  return value;
}
