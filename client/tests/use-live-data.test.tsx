import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/services/api", () => ({
  API_BASE_URL: "/backend",
  fetchMetrics: vi.fn(),
  fetchLatestBriefing: vi.fn(),
}));

import { fetchLatestBriefing, fetchMetrics } from "@/services/api";
import { LiveDataProvider, useLiveData } from "@/hooks/use-live-data";
import { snapshotFixture } from "./fixtures";
import type { AgentRunBriefing, LatestBriefingResponse } from "@/types/api";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  handlers: Record<string, (event: MessageEvent) => void> = {};
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, handler: (event: MessageEvent) => void) {
    this.handlers[name] = handler;
  }

  close() {
    this.closed = true;
  }

  emit(name: string, data: unknown) {
    this.handlers[name]?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  emitMessageEvent(name: string, data: unknown) {
    this.handlers[name]?.(new MessageEvent(name, { data: JSON.stringify(data) }));
  }

  static last(): FakeEventSource {
    const source = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    if (!source) throw new Error("no EventSource was opened");
    return source;
  }
}

function Reader() {
  const { snapshot, briefing, status, error, lastEventAt } = useLiveData();
  return (
    <div>
      <p data-testid="status">{status}</p>
      <p data-testid="revenue">{snapshot?.revenue_today_display ?? "none"}</p>
      <p data-testid="briefing">{briefing?.run_id ?? "none"}</p>
      <p data-testid="error">{error}</p>
      <p data-testid="last-event">{lastEventAt ?? "none"}</p>
    </div>
  );
}

function renderProvider() {
  return render(
    <LiveDataProvider>
      <Reader />
    </LiveDataProvider>,
  );
}

const metrics = vi.mocked(fetchMetrics);
const briefing = vi.mocked(fetchLatestBriefing);

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  metrics.mockResolvedValue(snapshotFixture());
  briefing.mockResolvedValue({ run_id: null, message: "no agent run yet" } as LatestBriefingResponse);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("useLiveData", () => {
  it("loads the first paint from the JSON endpoints and goes live on open", async () => {
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("revenue")).toHaveTextContent("$410"));
    expect(screen.getByTestId("status")).toHaveTextContent("connecting");
    expect(FakeEventSource.last().url).toBe("/backend/api/live");

    act(() => FakeEventSource.last().onopen?.());
    expect(screen.getByTestId("status")).toHaveTextContent("live");
  });

  it("applies a streamed snapshot and briefing without refetching", async () => {
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("revenue")).toHaveTextContent("$410"));
    expect(metrics).toHaveBeenCalledTimes(1);

    act(() =>
      FakeEventSource.last().emit("snapshot", snapshotFixture({ revenue_today_display: "$999" })),
    );
    expect(screen.getByTestId("revenue")).toHaveTextContent("$999");
    expect(screen.getByTestId("status")).toHaveTextContent("live");
    expect(screen.getByTestId("last-event")).not.toHaveTextContent("none");

    const run = { run_id: "run-7" } as unknown as AgentRunBriefing;
    act(() => FakeEventSource.last().emit("briefing", run));
    expect(screen.getByTestId("briefing")).toHaveTextContent("run-7");
    expect(metrics).toHaveBeenCalledTimes(1);
  });

  it("reports a server error event and marks the stream reconnecting", () => {
    renderProvider();
    const source = FakeEventSource.last();

    act(() => source.emitMessageEvent("error", { message: "Agent run failed" }));
    expect(screen.getByTestId("error")).toHaveTextContent("Agent run failed");

    act(() => source.onerror?.());
    expect(screen.getByTestId("status")).toHaveTextContent("reconnecting");
  });

  it("surfaces a failed first fetch", async () => {
    metrics.mockRejectedValue(new Error("Cannot reach the backend at /backend."));
    renderProvider();

    await waitFor(() =>
      expect(screen.getByTestId("error")).toHaveTextContent("Cannot reach the backend"),
    );
    expect(screen.getByTestId("revenue")).toHaveTextContent("none");
  });

  it("closes the stream on unmount", async () => {
    const view = renderProvider();
    await waitFor(() => expect(metrics).toHaveBeenCalled());
    const source = FakeEventSource.last();

    view.unmount();
    expect(source.closed).toBe(true);
  });

  it("says the stream is offline when EventSource does not exist", async () => {
    vi.stubGlobal("EventSource", undefined);
    renderProvider();

    // the first-paint fetch settles after the offline notice and clears the
    // message text, so only the connection status is observable here
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("offline"));
    expect(screen.getByTestId("revenue")).toHaveTextContent("$410");
  });
});

describe("useLiveData outside the provider", () => {
  it("refuses to read a context that is not there", () => {
    function Orphan() {
      useLiveData();
      return null;
    }

    expect(() => render(<Orphan />)).toThrow(/LiveDataProvider/);
  });
});
