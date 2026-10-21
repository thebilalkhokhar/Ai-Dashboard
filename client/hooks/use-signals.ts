"use client";

// Data fetching for GET /api/signals: the main report (without the stalled list)
// and the stalled orders the Pipeline tab asks for separately.
//
// Behaviour kept from the original dashboard: the first snapshot only sets the
// baseline, later generated_at changes refetch, an in-flight request is shared
// so StrictMode's double effect mount makes exactly one request, and every
// state write happens after the request settles.

import { useEffect, useRef, useState } from "react";
import { fetchSignals } from "@/services/api";
import type { SignalsQuery, SignalsReport, StalledOrder } from "@/types/api";

export const ROW_PAGE_SIZE = 25;

const DEFAULT_FILTERS: SignalsQuery = { staleDays: 3, stalledDays: 2, overdueDays: 30 };

export interface SignalsState {
  filters: SignalsQuery;
  signals: SignalsReport | null;
  signalsError: string;
  isLoadingSignals: boolean;
  stalledOrders: StalledOrder[] | null;
  stalledOrdersError: string;
  isLoadingStalledOrders: boolean;
  rowLimit: number;
  reloadKey: number;
  setRowLimit: (updater: (current: number) => number) => void;
  applyFilter: (patch: SignalsQuery) => void;
  retry: () => void;
  onTabChange: (tab: string) => void;
}

export function useSignals({
  activeTab,
  snapshotAt,
}: {
  activeTab: string;
  snapshotAt: string | null;
}): SignalsState {
  const [filters, setFilters] = useState<SignalsQuery>(DEFAULT_FILTERS);
  const [signals, setSignals] = useState<SignalsReport | null>(null);
  const [signalsError, setSignalsError] = useState("");
  const [isLoadingSignals, setIsLoadingSignals] = useState(true);
  const [rowLimit, setRowLimit] = useState(ROW_PAGE_SIZE);
  const [reloadKey, setReloadKey] = useState(0);
  const [stalledOrders, setStalledOrders] = useState<StalledOrder[] | null>(null);
  const [stalledOrdersError, setStalledOrdersError] = useState("");
  const [isLoadingStalledOrders, setIsLoadingStalledOrders] = useState(false);

  const hasRequestedSignalsRef = useRef(false);
  const previousFilterKeyRef = useRef(JSON.stringify(filters));
  const previousReloadKeyRef = useRef(reloadKey);
  const previousSnapshotAtRef = useRef<string | null>(null);
  const signalsRequestRef = useRef<{ key: string; promise: Promise<SignalsReport> } | null>(null);

  useEffect(() => {
    let isActive = true;
    const filterKey = JSON.stringify(filters);
    const filtersChanged = previousFilterKeyRef.current !== filterKey;
    previousFilterKeyRef.current = filterKey;
    const retryChanged = previousReloadKeyRef.current !== reloadKey;
    previousReloadKeyRef.current = reloadKey;
    const snapshotChanged =
      previousSnapshotAtRef.current !== null &&
      snapshotAt !== null &&
      previousSnapshotAtRef.current !== snapshotAt;
    if (snapshotAt !== null) previousSnapshotAtRef.current = snapshotAt;

    const shouldFetch =
      !hasRequestedSignalsRef.current || filtersChanged || retryChanged || snapshotChanged;
    const requestKey = `${filterKey}:${reloadKey}`;
    const inFlight = signalsRequestRef.current;
    if (!shouldFetch && !inFlight) return;

    const request =
      inFlight?.key === requestKey
        ? inFlight.promise
        : fetchSignals({ ...filters, includeStalled: false });
    if (request !== inFlight?.promise) {
      hasRequestedSignalsRef.current = true;
      signalsRequestRef.current = { key: requestKey, promise: request };
    }

    request
      .then((report) => {
        if (!isActive) return;
        setSignals(report);
        setSignalsError("");
      })
      .catch((error: unknown) => {
        if (!isActive) return;
        setSignalsError(
          error instanceof Error ? error.message : "Could not load the signals report.",
        );
      })
      .finally(() => {
        if (signalsRequestRef.current?.promise === request) {
          signalsRequestRef.current = null;
        }
        if (isActive) setIsLoadingSignals(false);
      });

    return () => {
      isActive = false;
    };
  }, [filters, reloadKey, snapshotAt]);

  useEffect(() => {
    if (activeTab !== "pipeline") return;
    let isActive = true;

    fetchSignals({ ...filters, includeStalled: true })
      .then((report) => {
        if (!isActive) return;
        setStalledOrders(report.stalled_orders.items);
        setStalledOrdersError("");
      })
      .catch((error: unknown) => {
        if (!isActive) return;
        setStalledOrdersError(
          error instanceof Error ? error.message : "Could not load stalled orders.",
        );
      })
      .finally(() => {
        if (isActive) setIsLoadingStalledOrders(false);
      });

    return () => {
      isActive = false;
    };
  }, [activeTab, filters, reloadKey, snapshotAt]);

  function applyFilter(patch: SignalsQuery) {
    setIsLoadingSignals(true);
    setRowLimit(ROW_PAGE_SIZE);
    if (activeTab === "pipeline") {
      setStalledOrders(null);
      setIsLoadingStalledOrders(true);
    }
    setFilters((current) => ({ ...current, ...patch }));
  }

  function retry() {
    setIsLoadingSignals(true);
    setSignalsError("");
    if (activeTab === "pipeline") {
      setStalledOrders(null);
      setStalledOrdersError("");
      setIsLoadingStalledOrders(true);
    }
    setReloadKey((current) => current + 1);
  }

  function onTabChange(tab: string) {
    setRowLimit(ROW_PAGE_SIZE);
    if (tab === "pipeline") {
      setStalledOrders(null);
      setStalledOrdersError("");
      setIsLoadingStalledOrders(true);
    }
  }

  return {
    filters,
    signals,
    signalsError,
    isLoadingSignals,
    stalledOrders,
    stalledOrdersError,
    isLoadingStalledOrders,
    rowLimit,
    reloadKey,
    setRowLimit,
    applyFilter,
    retry,
    onTabChange,
  };
}
