import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// Command Center must become the first, default tab WITHOUT disturbing the
// existing navigation, so mock the heavy panels and data hooks and assert only
// on the tab wiring.

vi.mock("@/hooks/use-live-data", () => ({
  useLiveData: () => ({
    snapshot: null,
    briefing: null,
    status: "live",
    error: "",
    lastEventAt: null,
    refresh: vi.fn(),
    setBriefing: vi.fn(),
  }),
}));

const onTabChange = vi.fn();
vi.mock("@/hooks/use-signals", () => ({
  ROW_PAGE_SIZE: 25,
  useSignals: () => ({
    filters: {}, signals: null, signalsError: "", isLoadingSignals: false,
    stalledOrders: null, stalledOrdersError: "", isLoadingStalledOrders: false,
    rowLimit: 25, reloadKey: 0, setRowLimit: vi.fn(), applyFilter: vi.fn(),
    retry: vi.fn(), onTabChange,
  }),
}));
vi.mock("@/hooks/use-stuck-leads", () => ({
  useStuckLeads: () => ({
    stuckLeads: null, stuckLeadsError: "", isLoadingStuckLeads: false,
    leadRowLimit: 25, setLeadRowLimit: vi.fn(), retry: vi.fn(),
  }),
}));
vi.mock("@/components/command-center/command-center", () => ({
  CommandCenter: () => <p>command-center-panel</p>,
}));
vi.mock("@/components/dashboard/overview-panel", () => ({
  OverviewPanel: () => <p>overview-panel</p>,
}));
vi.mock("@/components/dashboard/panels", () => ({
  LeadsPanel: () => <p>leads</p>,
  PipelinePanel: () => <p>pipeline</p>,
  ReceivablesPanel: () => <p>receivables</p>,
}));

import { Dashboard } from "@/components/dashboard";

describe("Dashboard navigation", () => {
  it("renders Command Center first and as the default tab, keeping the others", () => {
    render(<Dashboard actionCenter={<p>ai-review</p>} onSelectCustomer={vi.fn()} />);

    const triggers = screen.getAllByRole("tab");
    expect(triggers.map((tab) => tab.textContent)).toEqual([
      "Command Center",
      "Overview",
      "Receivables",
      "Pipeline",
      "Leads",
      "AI review",
      "Chats",
    ]);
    // the default landing content is the Command Center
    expect(screen.getByText("command-center-panel")).toBeInTheDocument();

    // navigating to the existing Overview tab still works
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(screen.getByText("overview-panel")).toBeInTheDocument();
    expect(onTabChange).toHaveBeenCalledWith("overview");
  });
});
