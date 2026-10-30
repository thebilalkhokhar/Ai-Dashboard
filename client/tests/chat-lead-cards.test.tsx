import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { ChatMarkdown } from "@/components/chat-markdown";

const LEADS_ANSWER = `Here are the leads that need follow-up (page 1 of 10).

- Rashad at Zeus Legacy — status: Attempted to Contact; 11 days idle; product: custom packaging; missing: quantity, size, material, finish; next_action: Ask Rashad for the missing quantity.
- Bryan at Bryan's Label — status: New; 3 days idle; product: labels; quantity: A small batch; size: 3 mL peptide vial; material: Waterproof and freezer safe; finish: soft touch; missing:; next_action: Ask Bryan for the artwork file.
- null at Not stated — status: New; 7 days idle; product: 500bages each; quantity: 500bages each; size: 4 x 6 In; missing: material, finish; next_action: Ask for the missing material.

Important: There are more records than this page shows. Ask for the next page and I will fetch them.

560 stuck leads match in total; 3 are on this page and 557 remain.`;

describe("ChatMarkdown lead cards", () => {
  it("renders each lead as a scannable card in the answer's order", () => {
    render(<ChatMarkdown text={LEADS_ANSWER} />);
    const list = screen.getByRole("list", { name: "Leads" });
    const cards = within(list).getAllByRole("listitem");
    expect(cards).toHaveLength(3);

    expect(within(cards[0]).getByText("Rashad")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Zeus Legacy")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Attempted to Contact")).toBeInTheDocument();
    expect(within(cards[0]).getByText("11 days idle")).toBeInTheDocument();
    expect(within(cards[0]).getByText("custom packaging")).toBeInTheDocument();
    expect(within(cards[0]).getAllByText("missing")).toHaveLength(4);
    expect(within(cards[0]).getByText("Next step")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Ask Rashad for the missing quantity.")).toBeInTheDocument();

    expect(within(cards[1]).getByText("Bryan")).toBeInTheDocument();
    expect(within(cards[1]).getByText("A small batch")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Waterproof and freezer safe")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Nothing missing")).toBeInTheDocument();

    expect(within(cards[2]).getByText("Name not provided")).toBeInTheDocument();
    expect(within(cards[2]).getByText("Not stated")).toBeInTheDocument();
    expect(within(cards[2]).getAllByText("500bages each")).toHaveLength(2);
  });

  it("never shows the raw next_action label", () => {
    const { container } = render(<ChatMarkdown text={LEADS_ANSWER} />);
    expect(container.textContent).not.toContain("next_action");
    expect(container.textContent).toContain("Next step");
  });

  it("keeps the more-records note out of the list and below the cards", () => {
    render(<ChatMarkdown text={LEADS_ANSWER} />);
    const list = screen.getByRole("list", { name: "Leads" });
    const note = screen.getByRole("note");
    expect(within(list).queryByText(/There are more records/)).not.toBeInTheDocument();
    expect(note).toHaveTextContent("There are more records than this page shows.");
    // the callout follows the card list in document order
    expect(list.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders the live pipe-delimited variant as cards", () => {
    render(
      <ChatMarkdown
        text={`Here are the leads that need follow-up (page 1 of 14). 10 of 136 shown; next page offset is 10.

Canyon Plastics | contact: Canyon | product: resealable bags | size: 6.5" w x 5.4"l | finish: gloss | missing: material | next_action: Ask Canyon for the missing material.
Not stated (JEAN LEDESMA) | contact: JEAN LEDESMA | product: Beef Jerky stand-up pouches | size: 5.5"x7.8" x 2 | missing: quantity, material, finish | next_action: Ask JEAN LEDESMA for the missing quantity.
(Lead) 6374650000073982016 | contact: null | product: Beef Jerky stand-up pouches | missing: quantity, size, material, finish | next_action: Ask null for the missing quantity.

Would you like me to pull the next page (offset 10) to show the next set of stuck leads?`}
      />,
    );
    const list = screen.getByRole("list", { name: "Leads" });
    const cards = within(list).getAllByRole("listitem");
    expect(cards).toHaveLength(3);

    expect(within(cards[0]).getByText("Canyon")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Canyon Plastics")).toBeInTheDocument();
    expect(within(cards[0]).getByText("gloss")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Ask Canyon for the missing material.")).toBeInTheDocument();

    expect(within(cards[1]).getByText("JEAN LEDESMA")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Not stated")).toBeInTheDocument();

    expect(within(cards[2]).getByText("Name not provided")).toBeInTheDocument();
    expect(within(cards[2]).getAllByText("missing")).toHaveLength(4);

    // the closing prose and the raw pipe labels stay out of the cards
    expect(screen.getByText(/Would you like me to pull the next page/)).toBeInTheDocument();
    expect(list.textContent).not.toContain("contact:");
    expect(list.textContent).not.toContain("next_action:");
  });

  it("leaves non-lead answers in readable markdown, not cards", () => {
    const { container } = render(
      <ChatMarkdown
        text={
          "Here is what the payments look like this week.\n\n**Total received:** $48,210.00 across 12 payments.\n\n- **Polkadot Labs** paid $12,500.00 on Oct 29.\n- **Cape Packaging** paid $8,940.00 on Oct 28."
        }
      />,
    );
    expect(screen.queryByRole("list", { name: "Leads" })).not.toBeInTheDocument();
    expect(container.querySelector(".cmd-list")).toBeInTheDocument();
    expect(screen.getByText("Total received:").tagName).toBe("STRONG");
  });

  it("does not turn a status/days line without lead fields into a card", () => {
    render(<ChatMarkdown text={"- INV-2041 — status: paid; days: 3; product: labels"} />);
    expect(screen.queryByRole("list", { name: "Leads" })).not.toBeInTheDocument();
  });

  it("keeps an Important note as plain prose when the message has no lead cards", () => {
    render(<ChatMarkdown text={"Important: there are more records in this list."} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.getByText(/More records|more records/)).toBeInTheDocument();
  });
});
