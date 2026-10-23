import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { AnswerProvenance } from "@/components/chat-provenance";
import type { AnswerEvidence } from "@/lib/presentation";
import type { ChatToolResultEvent } from "@/types/api";

const evidence: AnswerEvidence[] = [
  {
    event_id: "e1",
    tool: "get_customer360",
    label: "Reviewing the CRM 360 view",
    durationMs: 120,
    ok: true,
    at: "2026-10-24T00:00:00Z",
  },
];

const cardFrame: ChatToolResultEvent = {
  tool: "get_customer360",
  customer_card: {
    name: "Frank Jr",
    company: "Arellano Jerky",
    status: "New",
    email: "frank@example.com",
    phone: "2143923587",
    record_type: "Contact",
    counts: { projects: 2, estimates: 1, timeline: 56 },
    totals: { estimates_total_amount: 1400 },
    last_interaction: {
      type: "WhatsApp",
      at: "2026-10-21T15:12:31.000Z",
      direction: "Inbound",
    },
    last_message: "Too expensive",
  },
};

describe("AnswerProvenance customer card", () => {
  it("renders the 360 card from the backend's frame", () => {
    render(<AnswerProvenance evidence={evidence} frames={[cardFrame]} />);
    const card = screen.getByTestId("chat-360-card");
    expect(card).toHaveTextContent("Frank Jr");
    expect(card).toHaveTextContent("Arellano Jerky");
    expect(card).toHaveTextContent("frank@example.com · 2143923587");
    expect(card).toHaveTextContent("Projects 2");
    expect(card).toHaveTextContent("Interactions 56");
    expect(card).toHaveTextContent("Estimates $1,400");
    expect(card).toHaveTextContent("customer wrote last");
    expect(card).toHaveTextContent("“Too expensive”");
  });

  it("renders no card when no frame carries one", () => {
    render(<AnswerProvenance evidence={evidence} frames={[{ tool: "get_payments" }]} />);
    expect(screen.queryByTestId("chat-360-card")).toBeNull();
  });

  it("labels the CRM payments total as the CRM ledger's own", () => {
    const moneyFrame: ChatToolResultEvent = {
      tool: "get_crm_payments",
      money_label: "sum_amount_received",
      money_total: 20890,
      window: { date_from: "2026-10-23", date_to: "2026-10-23" },
    };
    render(<AnswerProvenance evidence={evidence} frames={[moneyFrame]} />);
    expect(screen.getByTestId("chat-proof-facts")).toHaveTextContent(
      "Total received · Zoho CRM $20,890",
    );
  });
});
