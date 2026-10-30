import { describe, expect, it } from "vitest";

import {
  displayLeadName,
  isImportantNote,
  parseLeadAnswerItem,
} from "@/lib/lead-answer";

const RASHAD =
  "Rashad at Zeus Legacy — status: Attempted to Contact; 11 days idle; product: custom packaging; missing: quantity, size, material, finish; next_action: Ask Rashad for the missing quantity.";

describe("parseLeadAnswerItem", () => {
  it("reads the known semicolon pattern without changing any value", () => {
    const lead = parseLeadAnswerItem(`- ${RASHAD}`);
    expect(lead).not.toBeNull();
    expect(lead!.name).toBe("Rashad");
    expect(lead!.company).toBe("Zeus Legacy");
    expect(lead!.status).toBe("Attempted to Contact");
    expect(lead!.idle).toBe("11 days idle");
    expect(lead!.product).toBe("custom packaging");
    expect(lead!.missing.map((item) => item.key)).toEqual([
      "quantity",
      "size",
      "material",
      "finish",
    ]);
    expect(lead!.specs).toEqual([
      { key: "quantity", label: "Quantity", value: null },
      { key: "size", label: "Size", value: null },
      { key: "material", label: "Material", value: null },
      { key: "finish", label: "Finish", value: null },
    ]);
    expect(lead!.nothingMissing).toBe(false);
    expect(lead!.nextAction).toBe("Ask Rashad for the missing quantity.");
  });

  it("reads the em-dash row shape with a lead id and company label", () => {
    const lead = parseLeadAnswerItem(
      "- Lead 6374650000073956008 — Thomas Wall Sr. — company: Flat pouch 25k — status: Attempted to Contact — product: Pouch — quantity: 25k — size: 4 x 6 In — missing: material, finish — days since activity: 3",
    );
    expect(lead).not.toBeNull();
    expect(lead!.reference).toBe("6374650000073956008");
    expect(lead!.name).toBe("Thomas Wall Sr.");
    expect(lead!.company).toBe("Flat pouch 25k");
    expect(lead!.idle).toBe("3 days idle");
    expect(lead!.specs).toContainEqual({ key: "quantity", label: "Quantity", value: "25k" });
    expect(lead!.specs).toContainEqual({ key: "size", label: "Size", value: "4 x 6 In" });
    expect(lead!.specs).toContainEqual({ key: "material", label: "Material", value: null });
    expect(lead!.specs).toContainEqual({ key: "finish", label: "Finish", value: null });
  });

  it("keeps odd source values exactly as written", () => {
    const lead = parseLeadAnswerItem(
      "- null at Not stated — status: New; 7 days idle; product: 500bages each; quantity: 500bages each; size: 4 x 6 In; missing: material, finish; next_action: Ask for the missing material.",
    );
    expect(lead).not.toBeNull();
    expect(lead!.name).toBe("null");
    expect(displayLeadName(lead!.name)).toBe("Name not provided");
    expect(lead!.company).toBe("Not stated");
    expect(lead!.product).toBe("500bages each");
    expect(lead!.specs).toContainEqual({
      key: "quantity",
      label: "Quantity",
      value: "500bages each",
    });
  });

  it("treats an empty/none missing list as nothing missing", () => {
    const empty = parseLeadAnswerItem(
      "Bryan at Bryan's Label — status: New; 3 days idle; product: labels; quantity: A small batch; size: 3 mL peptide vial; material: Waterproof and freezer safe; finish: soft touch; missing:; next_action: Ask Bryan for the artwork file.",
    );
    expect(empty).not.toBeNull();
    expect(empty!.nothingMissing).toBe(true);
    expect(empty!.missing).toEqual([]);
    expect(empty!.specs.find((item) => item.key === "quantity")?.value).toBe("A small batch");
    expect(empty!.nextAction).toBe("Ask Bryan for the artwork file.");

    expect(
      parseLeadAnswerItem("A at B — status: New; missing: none; next_action: Call A.")!.nothingMissing,
    ).toBe(true);
    expect(
      parseLeadAnswerItem("A at B — status: New; missing: (none); next_action: Call A.")!.nothingMissing,
    ).toBe(true);
  });

  it("reads uncoloned field shapes and a 1) list marker", () => {
    const lead = parseLeadAnswerItem(
      "21) Website Chat Visitor — Not stated — lead_id 637465000007381 — days 7 — product Bags — missing: quantity, size, material, finish",
    );
    expect(lead).not.toBeNull();
    expect(lead!.reference).toBe("637465000007381");
    expect(lead!.name).toBe("Website Chat Visitor");
    expect(lead!.company).toBe("Not stated");
    expect(lead!.idle).toBe("7 days idle");
    expect(lead!.product).toBe("Bags");
  });

  it("formats a single idle day and keeps decimals", () => {
    expect(
      parseLeadAnswerItem("A at B — status: New; days since activity: 1; missing: size")!.idle,
    ).toBe("1 day idle");
    expect(parseLeadAnswerItem("A at B — status: New; days: 3.2; missing: size")!.idle).toBe(
      "3.2 days idle",
    );
  });

  it("keeps a semicolon inside the action sentence", () => {
    const lead = parseLeadAnswerItem(
      "A at B — status: New; missing: size; next_action: Ask A for the size; then confirm the material.",
    );
    expect(lead!.nextAction).toBe("Ask A for the size; then confirm the material.");
  });

  it("finds next_action even when the model omits its separator", () => {
    const lead = parseLeadAnswerItem(
      "A at B — status: New; missing: size next_action: Ask A for the size.",
    );
    expect(lead).not.toBeNull();
    expect(lead!.missing.map((item) => item.key)).toEqual(["size"]);
    expect(lead!.nextAction).toBe("Ask A for the size.");
  });

  it("reads bold labels and a sentence-ending missing list", () => {
    const lead = parseLeadAnswerItem(
      "- **Rashad at Zeus Legacy** — **status:** New; **missing:** material, finish.; **next_action:** Ask Rashad for the material.",
    );
    expect(lead).not.toBeNull();
    expect(lead!.name).toBe("Rashad");
    expect(lead!.company).toBe("Zeus Legacy");
    expect(lead!.status).toBe("New");
    expect(lead!.missing.map((item) => item.key)).toEqual(["material", "finish"]);
    expect(lead!.nextAction).toBe("Ask Rashad for the material.");
  });

  it("reads the live pipe row: company first, person in contact:", () => {
    const lead = parseLeadAnswerItem(
      'Canyon Plastics | contact: Canyon | product: resealable bags | size: 6.5" w x 5.4"l | finish: gloss | missing: material | next_action: Ask Canyon for the missing material.',
    );
    expect(lead).not.toBeNull();
    expect(lead!.name).toBe("Canyon");
    expect(lead!.company).toBe("Canyon Plastics");
    expect(lead!.product).toBe("resealable bags");
    expect(lead!.specs).toContainEqual({
      key: "size",
      label: "Size",
      value: '6.5" w x 5.4"l',
    });
    expect(lead!.specs).toContainEqual({ key: "finish", label: "Finish", value: "gloss" });
    expect(lead!.specs).toContainEqual({ key: "material", label: "Material", value: null });
    expect(lead!.nextAction).toBe("Ask Canyon for the missing material.");
  });

  it("keeps the wrapped Not stated name readable", () => {
    const lead = parseLeadAnswerItem(
      'Not stated (JEAN LEDESMA) | contact: JEAN LEDESMA | product: Beef Jerky stand-up pouches | size: 5.5"x7.8" x 2 | missing: quantity, material, finish | next_action: Ask JEAN LEDESMA for the missing quantity.',
    );
    expect(lead).not.toBeNull();
    expect(lead!.name).toBe("JEAN LEDESMA");
    expect(lead!.company).toBe("Not stated");
  });

  it("reads a (Lead) id and a null contact without making the id a company", () => {
    const lead = parseLeadAnswerItem(
      "(Lead) 6374650000073982016 | contact: null | product: Beef Jerky stand-up pouches | missing: quantity, size, material, finish | next_action: Ask null for the missing quantity.",
    );
    expect(lead).not.toBeNull();
    expect(lead!.reference).toBe("6374650000073982016");
    expect(lead!.name).toBe("null");
    expect(lead!.company).toBeNull();
    expect(displayLeadName(lead!.name)).toBe("Name not provided");
  });

  it("does not repeat the person as the company", () => {
    const lead = parseLeadAnswerItem(
      "Camita | contact: Camita | product: custom Beef Jerky stand-up pouches | missing: quantity, size, material, finish | next_action: Ask Camita for the missing quantity.",
    );
    expect(lead).not.toBeNull();
    expect(lead!.name).toBe("Camita");
    expect(lead!.company).toBeNull();
  });

  it("does not turn ordinary record prose into a lead card", () => {
    expect(
      parseLeadAnswerItem("- Total received: $48,210.00 across 12 payments."),
    ).toBeNull();
    expect(
      parseLeadAnswerItem("- INV-2041 — status: paid; days: 3; product: labels"),
    ).toBeNull();
    expect(
      parseLeadAnswerItem("**Total received:** $48,210.00 across 12 payments."),
    ).toBeNull();
    expect(
      parseLeadAnswerItem("- **Polkadot Labs** paid $12,500.00 on Oct 29 against INV-2041."),
    ).toBeNull();
  });

  it("keeps an unknown labelled field as an extra instead of dropping it", () => {
    const lead = parseLeadAnswerItem(
      "A at B — status: New; missing: size; conversion_reason: Slow follow-up.",
    );
    expect(lead).not.toBeNull();
    expect(lead!.extras).toEqual([
      { label: "conversion_reason", value: "Slow follow-up." },
    ]);
  });
});

describe("isImportantNote", () => {
  it("recognises the more-records note in prose, bold and bullet form", () => {
    expect(isImportantNote("Important: There are more records than shown.")).toBe(true);
    expect(isImportantNote("**Important:** There are more records.")).toBe(true);
    expect(isImportantNote("- **Important:** There are more records.")).toBe(true);
    expect(isImportantNote("> Important: There are more records.")).toBe(true);
    expect(isImportantNote("Not important: this is prose.")).toBe(false);
  });
});
