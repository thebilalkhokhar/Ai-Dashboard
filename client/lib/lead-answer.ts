// Structured reading of the lead-list answers the assistant produces.
//
// The backend prompt lets the model write one line per lead, with labels
// inline. The exact separators vary (em dashes, semicolons, or pipes before
// the live views), so this parser reads labels, not positions. It only claims
// a line when the line carries lead-only fields (missing / next action)
// together with a status, a lead id, or an explicit contact.

export type LeadSpecKey = "quantity" | "size" | "material" | "finish" | "artwork";

export interface LeadSpec {
  key: LeadSpecKey;
  label: string;
  value: string | null;
}

export interface LeadMissing {
  key: string;
  label: string;
}

export interface LeadCard {
  name: string | null;
  company: string | null;
  status: string | null;
  idle: string | null;
  product: string | null;
  specs: LeadSpec[];
  missing: LeadMissing[];
  nothingMissing: boolean;
  nextAction: string | null;
  extras: { label: string; value: string }[];
  reference: string | null;
}

type FieldKey =
  | "status"
  | "idle"
  | "product"
  | "quantity"
  | "size"
  | "material"
  | "finish"
  | "artwork"
  | "missing"
  | "next"
  | "company"
  | "name";

const FIELD_BY_LABEL: Record<string, FieldKey> = {
  status: "status",
  "days since activity": "idle",
  "days since last activity": "idle",
  "days since contact": "idle",
  "days since last contact": "idle",
  "days idle": "idle",
  "idle days": "idle",
  days: "idle",
  idle: "idle",
  product: "product",
  "product type": "product",
  quantity: "quantity",
  qty: "quantity",
  size: "size",
  dimensions: "size",
  dimension: "size",
  material: "material",
  finish: "finish",
  artwork: "artwork",
  missing: "missing",
  "missing fields": "missing",
  "next action": "next",
  "next step": "next",
  company: "company",
  contact: "name",
  "contact name": "name",
  name: "name",
};

const SPEC_LABELS: Record<LeadSpecKey, string> = {
  quantity: "Quantity",
  size: "Size",
  material: "Material",
  finish: "Finish",
  artwork: "Artwork",
};

const MISSING_ALIASES: Record<string, LeadSpecKey> = {
  quantity: "quantity",
  qty: "quantity",
  size: "size",
  dimensions: "size",
  dimension: "size",
  material: "material",
  finish: "finish",
  artwork: "artwork",
};

function normaliseLabel(label: string): string {
  return label
    .toLowerCase()
    .replace(/[_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanSegment(segment: string): string {
  return segment.replace(/\s+/g, " ").trim();
}

function normaliseLine(line: string): string {
  return line
    .replace(/\r/g, "")
    .replace(/^\s*>\s?/, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "")
    .replace(/\*\*/g, "")
    .trim();
}

function splitSegments(body: string): string[] {
  // Fields are written with em dashes, semicolons or pipes. A plain hyphen is
  // left alone: it occurs inside names and values ("stand-up pouch").
  return body
    .split(/\s*[—–]\s*|\s*;\s*|\s*\|\s*|\s+·\s+/)
    .map(cleanSegment)
    .filter(Boolean);
}

function takeNextAction(body: string): { before: string; action: string | null } {
  // The action is the last field, and its sentence may itself contain the same
  // separators the other fields use. Cut it out whole instead of re-joining
  // fragments, so no value is reworded.
  const match = /(?:^|[\s—–;·|]+)(next[\s_-]*(?:action|step))\s*:\s*([\s\S]*)$/i.exec(body);
  if (!match) return { before: body, action: null };
  return {
    before: body.slice(0, match.index),
    action: match[2].trim(),
  };
}

function leadReference(segment: string): string | null {
  const parenthesised = /^\(\s*lead\s*\)\s*[:#]?\s*([\w-]+)$/i.exec(segment);
  if (parenthesised) return parenthesised[1];
  const labelled = /^lead\s*(?:id|#)?\s*[:#]\s*([\w-]+)$/i.exec(segment);
  if (labelled) return labelled[1];
  const underscored = /^lead[\s_-]*id\s+([\w-]+)$/i.exec(segment);
  if (underscored) return underscored[1];
  const numeric = /^lead\s+(\d[\w-]*)$/i.exec(segment);
  return numeric ? numeric[1] : null;
}

function splitIdentity(parts: string[]): { name: string | null; company: string | null } {
  const clean = parts.map((part) => part.trim()).filter(Boolean);
  if (clean.length === 0) return { name: null, company: null };
  if (clean.length >= 2) {
    return { name: clean[0], company: clean.slice(1).join(" · ") };
  }
  const single = clean[0];
  const at = /^(.*?)\s+at\s+(.+)$/i.exec(single);
  if (at) return { name: at[1].trim(), company: at[2].trim() };
  const comma = /^([^,]+),\s*(.+)$/.exec(single);
  if (comma && !/^(jr|sr|ii|iii|iv|phd|md)\.?$/i.test(comma[2].trim())) {
    return { name: comma[1].trim(), company: comma[2].trim() };
  }
  return { name: single, company: null };
}

function formatIdle(value: string): string {
  const raw = value.trim();
  if (!raw) return "";
  const number = /^(\d+(?:\.\d+)?)\b/.exec(raw);
  if (!number) return raw;
  const days = number[1];
  return `${days} ${days === "1" ? "day" : "days"} idle`;
}

function parseMissing(value: string): string[] {
  const raw = value.trim().replace(/^\((.*)\)$/, "$1").trim();
  if (
    !raw ||
    /^(?:none|nothing|nothing missing|no missing(?: fields)?|n\/a|na|-|—|–)\.?$/i.test(raw)
  ) {
    return [];
  }
  return raw
    .split(/\s*(?:,|;|\/|\band\b|&)\s*/i)
    .map((item) => item.trim().replace(/[.;:]+$/, ""))
    .filter(Boolean);
}

function missingLabel(key: string): string {
  return key.replace(/_/g, " ");
}

export function parseLeadAnswerItem(line: string): LeadCard | null {
  const normalised = normaliseLine(line);
  if (!normalised) return null;
  const { before, action } = takeNextAction(normalised);
  const segments = splitSegments(before);
  if (segments.length === 0) return null;

  const fields = new Map<FieldKey, string>();
  let reference: string | null = null;
  const identity: string[] = [];
  const extras: { label: string; value: string }[] = [];
  const nextAction = action;
  let sawLeadField = false;

  for (const segment of segments) {
    const leadId = leadReference(segment);
    if (leadId) {
      reference = leadId;
      continue;
    }

    const labelled = /^([A-Za-z][A-Za-z _-]{0,30})\s*:\s*(.*)$/.exec(segment);
    if (labelled) {
      const label = normaliseLabel(labelled[1]);
      const key = FIELD_BY_LABEL[label];
      if (key) {
        fields.set(key, labelled[2].trim());
        sawLeadField = true;
      } else {
        extras.push({ label: labelled[1].trim(), value: labelled[2].trim() });
      }
      continue;
    }

    const bareIdle = /^(\d+(?:\.\d+)?)\s*(?:d|days?)\s+(?:idle|since\b.*)$/i.exec(segment);
    if (bareIdle) {
      fields.set("idle", bareIdle[1]);
      sawLeadField = true;
      continue;
    }

    const spaced = /^([A-Za-z][A-Za-z _-]{0,24})\s+(\S.*)$/.exec(segment);
    if (spaced) {
      const label = normaliseLabel(spaced[1]);
      const key = FIELD_BY_LABEL[label];
      if (key) {
        fields.set(key, spaced[2].trim());
        sawLeadField = true;
        continue;
      }
    }

    if (sawLeadField) {
      extras.push({ label: "", value: segment });
    } else {
      identity.push(segment);
    }
  }

  const status = fields.get("status")?.trim() || null;
  const hasMissing = fields.has("missing");
  const hasNext = nextAction !== null;
  const isLead =
    (status !== null && (hasMissing || hasNext)) ||
    (hasMissing && hasNext) ||
    (reference !== null && (status !== null || hasMissing));
  if (!isLead) return null;

  const identityParts = [...identity];
  if (identityParts.length > 1 && /^lead$/i.test(identityParts[0])) identityParts.shift();
  const explicitName = fields.get("name")?.trim() || null;
  const explicitCompany = fields.get("company")?.trim() || null;
  const parsedIdentity = splitIdentity(identityParts);
  const identityText = identityParts.length === 1 ? identityParts[0].trim() : null;

  let name = explicitName ?? parsedIdentity.name;
  let company = explicitCompany ?? parsedIdentity.company;

  if (identityText) {
    // "Not stated (Name)" carries both values in one label; show the person
    // and keep the stated company, instead of making the whole string a name.
    const wrapped = /^not stated\s*\((.*)\)$/i.exec(identityText);
    if (wrapped) {
      if (!explicitName) name = wrapped[1].trim();
      if (!explicitCompany) company = "Not stated";
    } else if (
      explicitName &&
      !explicitCompany &&
      parsedIdentity.company === null &&
      parsedIdentity.name &&
      parsedIdentity.name.trim().toLowerCase() !== explicitName.toLowerCase()
    ) {
      // "Company | contact: Name | ..." puts the company first and the person
      // in the contact field.
      company = parsedIdentity.name.trim();
    }
  }

  if (company && /^(null|undefined)$/i.test(company.trim())) company = null;

  const productValue = fields.get("product")?.trim() ?? "";
  const product = productValue && !/^(null|undefined)$/i.test(productValue) ? productValue : null;

  const idleValue = fields.get("idle");
  const idle = idleValue === undefined ? null : formatIdle(idleValue) || null;

  const missing = hasMissing ? parseMissing(fields.get("missing") ?? "") : [];
  const missingKnownKeys = new Set<LeadSpecKey>();
  const extraMissing: LeadMissing[] = [];
  for (const item of missing) {
    const key = MISSING_ALIASES[normaliseLabel(item)];
    if (key) missingKnownKeys.add(key);
    else extraMissing.push({ key: normaliseLabel(item), label: missingLabel(item) });
  }

  const specKeys: LeadSpecKey[] = ["quantity", "size", "material", "finish", "artwork"];
  const specs: LeadSpec[] = [];
  const knownMissing: LeadMissing[] = [];
  for (const key of specKeys) {
    const value = fields.get(key)?.trim() ?? "";
    if (missingKnownKeys.has(key)) {
      specs.push({ key, label: SPEC_LABELS[key], value: null });
      knownMissing.push({ key, label: SPEC_LABELS[key] });
    } else if (value && !/^(null|undefined)$/i.test(value)) {
      specs.push({ key, label: SPEC_LABELS[key], value });
    }
  }

  return {
    name,
    company,
    status,
    idle,
    product,
    specs,
    missing: [...knownMissing, ...extraMissing],
    nothingMissing: hasMissing && missing.length === 0,
    nextAction,
    extras,
    reference,
  };
}

export function isImportantNote(line: string): boolean {
  const body = line
    .replace(/^\s*>\s?/, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "")
    .replace(/\*\*/g, "")
    .trim();
  return /^important\s*:/i.test(body);
}

export function displayLeadName(name: string | null): string {
  if (!name || /^(?:null|undefined|\(null\))$/i.test(name.trim())) return "Name not provided";
  return name;
}
