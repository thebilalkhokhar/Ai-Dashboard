"use client";

// Data fetching for GET /api/leads/stuck, which feeds the Overview card and the
// Leads tab. Fetched once per reload generation; the in-flight promise is shared
// so StrictMode's double effect mount makes one request, and all state is
// written only after the request settles.

import { useEffect, useRef, useState } from "react";
import { fetchStuckLeads } from "@/services/api";
import type { StuckLeadsResponse } from "@/types/api";
import { ROW_PAGE_SIZE } from "@/hooks/use-signals";

export interface StuckLeadsState {
  stuckLeads: StuckLeadsResponse | null;
  stuckLeadsError: string;
  isLoadingStuckLeads: boolean;
  leadRowLimit: number;
  setLeadRowLimit: (updater: (current: number) => number) => void;
  retry: () => void;
}

export function useStuckLeads({
  activeTab,
  reloadKey,
  reload,
}: {
  activeTab: string;
  reloadKey: number;
  reload: () => void;
}): StuckLeadsState {
  const [stuckLeads, setStuckLeads] = useState<StuckLeadsResponse | null>(null);
  const [stuckLeadsError, setStuckLeadsError] = useState("");
  const [isLoadingStuckLeads, setIsLoadingStuckLeads] = useState(true);
  const [leadRowLimit, setLeadRowLimit] = useState(ROW_PAGE_SIZE);
  const requestRef = useRef<Promise<StuckLeadsResponse> | null>(null);
  const loadedForReloadRef = useRef<number | null>(null);

  useEffect(() => {
    if (activeTab !== "leads" && activeTab !== "overview") return undefined;
    const alreadyLoaded =
      loadedForReloadRef.current === reloadKey &&
      (stuckLeads !== null || stuckLeadsError !== "");
    const inFlight = requestRef.current;
    if (alreadyLoaded && !inFlight) return undefined;

    let isActive = true;
    const request = inFlight ?? fetchStuckLeads();
    requestRef.current = request;
    request
      .then((result) => {
        loadedForReloadRef.current = reloadKey;
        if (!isActive) return;
        setStuckLeads(result);
        setStuckLeadsError("");
      })
      .catch((error: unknown) => {
        loadedForReloadRef.current = reloadKey;
        if (!isActive) return;
        setStuckLeadsError(
          error instanceof Error ? error.message : "Could not load stuck leads.",
        );
      })
      .finally(() => {
        if (requestRef.current === request) {
          requestRef.current = null;
        }
        if (isActive) setIsLoadingStuckLeads(false);
      });

    return () => {
      isActive = false;
    };
  }, [activeTab, reloadKey, stuckLeads, stuckLeadsError]);

  function retry() {
    setIsLoadingStuckLeads(true);
    setStuckLeadsError("");
    reload();
  }

  return {
    stuckLeads,
    stuckLeadsError,
    isLoadingStuckLeads,
    leadRowLimit,
    setLeadRowLimit,
    retry,
  };
}
