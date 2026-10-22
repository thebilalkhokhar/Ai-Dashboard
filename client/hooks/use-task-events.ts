"use client";

// useTaskEvents - one controlled, resumable connection to
// GET /api/tasks/{taskId}/events?after={seq}.
//
// It replays persisted events, then keeps streaming live ones. A cursor (the
// highest processed seq) is preserved across reconnects, duplicates are dropped
// by the reducer, reconnection uses capped exponential backoff (never an
// uncontrolled loop), and the whole connection is aborted when the task changes
// or the component unmounts. Stream/connection state lives in the same reducer
// so the effect only dispatches.

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApiError, openTaskEventStream } from "@/services/api";
import { commandCenterReducer, initialState, type StreamStatus } from "@/lib/task-events";
import type { TaskEvent } from "@/types/api";

const MAX_RETRIES = 6;
const BASE_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15000;

export function useTaskEvents(taskId: string | null) {
  const [state, dispatch] = useReducer(commandCenterReducer, initialState(taskId));

  const cursorRef = useRef(0);
  const attemptsRef = useRef(0);

  // A manual retry bumps the epoch to re-run the connection effect and reset
  // the backoff, without restarting on ordinary renders.
  const [epoch, setEpoch] = useState(0);
  const retry = useCallback(() => {
    attemptsRef.current = 0;
    setEpoch((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!taskId) {
      dispatch({ type: "reset", taskId: null });
      dispatch({ type: "stream", status: "idle" });
      return;
    }

    dispatch({ type: "reset", taskId });
    dispatch({ type: "stream", status: "connecting" });
    cursorRef.current = 0;
    attemptsRef.current = 0;

    let active = true;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    function setStream(status: StreamStatus, error = "") {
      if (active) dispatch({ type: "stream", status, error });
    }

    function scheduleReconnect(message: string) {
      if (!active) return;
      attemptsRef.current += 1;
      if (attemptsRef.current > MAX_RETRIES) {
        setStream("offline", message);
        return;
      }
      const backoff = Math.min(
        MAX_BACKOFF_MS,
        BASE_BACKOFF_MS * 2 ** (attemptsRef.current - 1),
      );
      setStream("reconnecting", message);
      timer = setTimeout(() => void connect(), backoff);
    }

    async function connect() {
      if (!active) return;
      controller = new AbortController();
      try {
        await openTaskEventStream(taskId as string, cursorRef.current, {
          signal: controller.signal,
          onEvent: (event: TaskEvent) => {
            if (!active || event.task_id !== taskId) return;
            dispatch({ type: "event", event });
            cursorRef.current = Math.max(cursorRef.current, event.seq);
            attemptsRef.current = 0;
            setStream("live");
          },
        });
        // Stream ended without an error status: resume from the cursor.
        if (active) scheduleReconnect("The task-event stream ended.");
      } catch (error) {
        if (!active || isAbort(error)) return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          setStream("error", "Not authorised to read this task stream.");
          return;
        }
        if (error instanceof ApiError && error.status === 404) {
          setStream("error", "This task has no recorded events.");
          return;
        }
        scheduleReconnect(
          error instanceof ApiError && error.message
            ? error.message
            : "Connection to the task-event stream was lost.",
        );
      }
    }

    void connect();

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      if (controller) controller.abort();
    };
  }, [taskId, epoch]);

  return useMemo(
    () => ({ ...state, status: state.streamStatus, error: state.streamError, retry }),
    [state, retry],
  );
}

function isAbort(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}
