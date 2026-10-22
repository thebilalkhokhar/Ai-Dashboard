import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { ChatMarkdown } from "@/components/chat-markdown";
import { OperationPanel } from "@/components/command-center/operation-panel";

describe("ChatMarkdown", () => {
  it("leaves a plain answer as one paragraph of the same text", () => {
    const { container } = render(<ChatMarkdown text="There are 42 overdue invoices." />);
    // other tests count this message once; the renderer must not split it
    expect(screen.getAllByText("There are 42 overdue invoices.")).toHaveLength(1);
    expect(container.querySelector("p")).toBeInTheDocument();
  });

  it("renders bullet and numbered lists as real lists", () => {
    const { container } = render(<ChatMarkdown text={"- first\n- second\n\n1. one\n2. two"} />);
    expect(container.querySelectorAll("ul, ol")).toHaveLength(2);
    expect(container.querySelector("ul")?.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("ol")?.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByText("first").tagName).toBe("LI");
    expect(screen.getByText("two").tagName).toBe("LI");
    // preflight strips markers, so the ordered variant must carry its own class
    expect(container.querySelector("ol")).toHaveClass("cmd-list--ordered");
    expect(container.querySelector("ul")).not.toHaveClass("cmd-list--ordered");
  });

  it("renders a pipe table with headers and cells", () => {
    render(<ChatMarkdown text={"| Customer | Amount |\n| --- | --- |\n| Polkadot | $4,210 |"} />);
    const table = screen.getByRole("table");
    expect(screen.getByRole("columnheader", { name: "Customer" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "$4,210" })).toBeInTheDocument();
    expect(table).toBeInTheDocument();
  });

  it("renders inline emphasis, code and safe links", () => {
    render(<ChatMarkdown text={"**Paid** _in full_ via `ACH` — [detail](https://erp.example/x)"} />);
    expect(screen.getByText("Paid").tagName).toBe("STRONG");
    expect(screen.getByText("in full").tagName).toBe("EM");
    expect(screen.getByText("ACH").tagName).toBe("CODE");
    const link = screen.getByRole("link", { name: "detail" });
    expect(link).toHaveAttribute("href", "https://erp.example/x");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
  });

  it("keeps fenced code verbatim without markdown-parsing it", () => {
    render(<ChatMarkdown text={"```\nSELECT * -- not a list\n```"} />);
    expect(screen.getByText("SELECT * -- not a list").tagName).toBe("CODE");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  // The model's text is untrusted input: it must never become markup.
  it("renders raw HTML as literal text", () => {
    const { container } = render(
      <ChatMarkdown text={'<img src=x onerror="alert(1)"> <script>alert(2)</script>'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
  });

  it("refuses a javascript: link", () => {
    render(<ChatMarkdown text={"[click](javascript:alert(1))"} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("[click](javascript:alert(1))")).toBeInTheDocument();
  });
});

describe("OperationPanel strip", () => {
  const base = {
    execution: "idle" as const,
    timeline: [],
    agents: {},
    streamStatus: "idle",
  };

  it("stays collapsed when nothing is running, so the chat owns the height", () => {
    render(<OperationPanel turn={null} {...base} />);
    expect(screen.getByRole("region", { name: "Current operation" })).toHaveAttribute(
      "data-open",
      "false",
    );
    expect(screen.queryByText("Ask a question to start a live operation.")).not.toBeInTheDocument();
    // the strip still states the phase, which is the only thing worth the space
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("stays one line tall during a live request, so the chat keeps its height", () => {
    render(
      <OperationPanel
        turn={{ turnId: "t1", prompt: "Show overdue accounts", taskId: "task-1", phase: "starting", message: "", startedAt: 0 }}
        execution="running"
        timeline={[
          {
            event_id: "e2",
            seq: 2,
            ts: "2026-10-21T15:00:02Z",
            type: "agent.started",
            agent_id: "chat",
            summary: "Started",
            human: "Understanding your request",
          },
        ]}
        agents={{}}
        streamStatus="live"
      />,
    );
    const region = screen.getByRole("region", { name: "Current operation" });
    // it used to swing open on its own and push the conversation below the fold
    expect(region).toHaveAttribute("data-open", "false");
    expect(screen.queryByText("Show overdue accounts")).not.toBeInTheDocument();
    // while still stating the phase and the assistant it is assigned to
    // exactly one mention of the assistant, never the phase and a peek saying it twice
    expect(within(region).getAllByText(/Assigned to/)).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: /show detail/i }));
    expect(screen.getByRole("region", { name: "Current operation" })).toHaveAttribute(
      "data-open",
      "true",
    );
    expect(screen.getByText("Show overdue accounts")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /hide detail/i }));
    expect(screen.getByRole("region", { name: "Current operation" })).toHaveAttribute(
      "data-open",
      "false",
    );
  });

  it("can be opened while idle", () => {
    render(<OperationPanel turn={null} {...base} />);
    fireEvent.click(screen.getByRole("button", { name: /show detail/i }));
    expect(screen.getByRole("region", { name: "Current operation" })).toHaveAttribute(
      "data-open",
      "true",
    );
    expect(screen.getByText("Ask a question to start a live operation.")).toBeInTheDocument();
  });
});
