import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

type Stream = {
  taskId: string;
  after: number;
  signal: AbortSignal;
  onEvent: (event: unknown) => void;
  resolve: () => void;
  reject: (error: unknown) => void;
};

// The hook's only real dependency is the streaming API; mock it so we can feed
// deterministic events and drive reconnection without a network or timer race.
const h = vi.hoisted(() => {
  const streams: unknown[] = [];
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }
  return { streams, ApiError };
});

vi.mock("@/services/api", () => ({
  ApiError: h.ApiError,
  openTaskEventStream: vi.fn(
    (taskId: string, after: number, options: { signal: AbortSignal; onEvent: (e: unknown) => void }) => {
      let resolve!: () => void;
      let reject!: (error: unknown) => void;
      const promise = new Promise<void>((res, rej) => {
        resolve = res;
        reject = rej;
      });
      h.streams.push({ taskId, after, signal: options.signal, onEvent: options.onEvent, resolve, reject });
      return promise;
    },
  ),
}));

import { openTaskEventStream } from "@/services/api";
import { useTaskEvents } from "@/hooks/use-task-events";
import { taskEvent } from "./fixtures";

const ApiError = h.ApiError;

function streams() {
  return h.streams as unknown as Stream[];
}

beforeEach(() => {
  h.streams.length = 0;
  vi.mocked(openTaskEventStream).mockClear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useTaskEvents", () => {
  it("opens at the replay cursor and applies live events", () => {
    const { result } = renderHook(() => useTaskEvents("run-1"));

    expect(openTaskEventStream).toHaveBeenCalledTimes(1);
    expect(streams()[0].after).toBe(0);

    act(() => {
      streams()[0].onEvent(taskEvent({ type: "task.started", task_id: "run-1", seq: 1 }));
    });
    act(() => {
      streams()[0].onEvent(taskEvent({ type: "agent.started", task_id: "run-1", agent_id: "collections", seq: 2 }));
    });

    expect(result.current.taskStatus).toBe("running");
    expect(result.current.status).toBe("live");
    expect(result.current.lastSeq).toBe(2);
    expect(result.current.agents.collections?.status).toBe("running");
  });

  it("drops duplicate events and ignores stale-task events", () => {
    const { result } = renderHook(() => useTaskEvents("run-1"));
    const dup = taskEvent({ type: "agent.progress", task_id: "run-1", agent_id: "chat", seq: 3, event_id: "same" });

    act(() => streams()[0].onEvent(dup));
    act(() => streams()[0].onEvent(dup));
    act(() => streams()[0].onEvent(taskEvent({ type: "task.completed", task_id: "other", seq: 4 })));

    expect(result.current.timeline).toHaveLength(1);
    expect(result.current.taskStatus).toBe("idle"); // 'other' event ignored, no task lifecycle for run-1 yet
  });

  it("reconnects from the last cursor with backoff when the stream ends", async () => {
    const { result } = renderHook(() => useTaskEvents("run-1"));

    act(() => {
      streams()[0].onEvent(taskEvent({ type: "agent.progress", task_id: "run-1", agent_id: "chat", seq: 7 }));
    });
    // stream ends cleanly
    await act(async () => {
      streams()[0].resolve();
      await Promise.resolve();
    });
    expect(result.current.status).toBe("reconnecting");

    // advance the first backoff window (500ms) -> reopen from the cursor
    await act(async () => {
      vi.advanceTimersByTime(600);
      await Promise.resolve();
    });
    expect(openTaskEventStream).toHaveBeenCalledTimes(2);
    expect(streams()[1].after).toBe(7);
    expect(streams()[1].taskId).toBe("run-1");
  });

  it("surfaces a permanent error (404 / 401) instead of pretending to be live", async () => {
    const { result } = renderHook(() => useTaskEvents("missing"));
    await act(async () => {
      streams()[0].reject(new ApiError("unknown task", 404));
      await Promise.resolve();
    });
    expect(result.current.status).toBe("error");
    expect(result.current.error).toMatch(/no recorded events/i);
    // no runaway reconnect loop
    expect(openTaskEventStream).toHaveBeenCalledTimes(1);

    const auth = renderHook(() => useTaskEvents("secret"));
    await act(async () => {
      streams()[1].reject(new ApiError("unauthorised", 401));
      await Promise.resolve();
    });
    expect(auth.result.current.status).toBe("error");
    expect(auth.result.current.error).toMatch(/authoris/i);
  });

  it("backs off a 503 capacity limit and stops after the retry budget", async () => {
    const { result } = renderHook(() => useTaskEvents("run-1"));

    for (let attempt = 0; attempt <= 7; attempt += 1) {
      const current = streams()[streams().length - 1];
      await act(async () => {
        current.reject(new ApiError("too many connections", 503));
        await Promise.resolve();
      });
      await act(async () => {
        vi.runAllTimers();
        await Promise.resolve();
      });
    }
    expect(result.current.status).toBe("offline");
  });

  it("aborts the old stream when the task changes and cleans up on unmount", () => {
    const { result, rerender, unmount } = renderHook(
      ({ id }: { id: string }) => useTaskEvents(id),
      { initialProps: { id: "run-a" } },
    );

    const first = streams()[0];
    expect(first.signal.aborted).toBe(false);

    rerender({ id: "run-b" });
    expect(first.signal.aborted).toBe(true);
    expect(streams()[1].taskId).toBe("run-b");
    expect(streams()[1].after).toBe(0);
    expect(result.current.timeline).toHaveLength(0);

    const second = streams()[1];
    unmount();
    expect(second.signal.aborted).toBe(true);
  });
});
