"use client";

// The registered agent list, shared by the Command Center and the execution
// overlay so neither keeps its own copy of who exists. Statuses come from the
// backend's persisted events; an agent that has never run stays idle.

import { useCallback, useEffect, useState } from "react";
import { fetchAgents } from "@/services/api";
import type { RegistryAgent } from "@/types/api";

export function useAgents() {
  const [agents, setAgents] = useState<RegistryAgent[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await fetchAgents();
      setAgents(response.agents);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the agent list.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      const response = await fetchAgents()
        .then((value) => ({ ok: true as const, value }))
        .catch((cause: unknown) => ({ ok: false as const, cause }));
      if (!active) return;
      if (response.ok) {
        setAgents(response.value.agents);
        setError("");
      } else {
        setError(
          response.cause instanceof Error
            ? response.cause.message
            : "Could not load the agent list.",
        );
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  return { agents, error, refresh, isLoading };
}
