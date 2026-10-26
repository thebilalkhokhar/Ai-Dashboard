# Golden regression questions — Ai-Dashboard chat

Run this set in chat after EVERY prompt or tool change, before the change
reaches the user. A change that breaks any row below is not shipped until
the row passes again. Numbers marked "window-dependent" are re-checked
against the tool output of that run, not memorised: the tool total of
that run is the expected value.

## How to run

Ask each question exactly as written, in a fresh chat where noted, and
compare the answer with the "Must show" column. Do not accept a
paraphrase that drops a ledger, a total, or the blocker.

## Payments — the combined tool must be used (one call, both ledgers)

| # | Ask | Must show |
|---|-----|-----------|
| P1 | What did customers pay today? | ERP total AND CRM total, each under its own label, for the ERP business day (the same day the revenue tile reports). Never $0 from one ledger alone while the other holds money. |
| P2 | What did customers pay this week? | ERP total AND CRM total, separate, never added together. |
| P3 | TREATDAY LLC ki sari payments show karo. | ERP $10,201 across 10 payments AND CRM $10,201 across 10 payments, every row with the same amount on both sides (row 9: $1,250 on 2025-11-06, payment 4986 — never "$1?"). Baseline verified Oct 27, 2026. |
| P4 | TREATDAY ke CRM payments dikhao. | All-time CRM total $10,201 / 10 payments — NOT the last-30-days $1,250. Root cause fixed Oct 27: the dateless call used to die in argument validation. |
| P5 | Canyon Plastics ki payments dikhao. | ERP leg AND CRM leg, both reported. "CRM payments have not been checked yet" is a FAIL — the combined tool makes a skipped leg impossible. |
| P6 | Show me all payments for TREATDAY LLC. | The 10-payment list, not a single invoice row. |

## Invoices / quotes / orders — status words must not zero the answer

| # | Ask | Must show |
|---|-----|-----------|
| Q1 | Which invoices are overdue? | Overdue list, no duplicated invoice ref, total from Python. |
| Q2 | Show me unpaid invoices. | Balance > 0 invoices. "None found (0)" while overdue balances exist is a FAIL — "unpaid" is a balance, not an ERP label. |
| Q3 | How many quotes are open? | A non-zero count matching the stale/open quotes, not 0 — "open" means not in a closed status, not a label containing the word "open". |
| Q4 | Which quotes are stale? | Stale list with quote ids written as list items (e.g. "- 20550: ...") — the guard must not discard the answer for its ids. |
| Q5 | Which orders are stalled? | Stalled orders with their money field labelled (balance vs total must not be swapped between answers). |

## Leads — the tool asks the question, the model must not invent a spec

| # | Ask | Must show |
|---|-----|-----------|
| L1 | Show me the leads that need follow-up. | Stuck leads with next_action on each row; page 2 must not repeat page 1 rows. |
| L2 | Show me the next page. | Continuation of L1 with the same filters, offset = previous next_offset, no repeated rows. |
| L3 | Orthoza lead ko order mein kaise convert karein? | First line: blocker "material". The closing question must be the tool's next_action ("Ask Orthoza for the missing material."). Must NOT recommend a material (foil / standard vinyl / any substrate) and must NOT offer to contact Orthoza. |
| L4 | Orthoza lead ka next action kya hai? | Same as L3: blocker first, next_action as the question, no invented spec, no outreach offer. |

## CRM records, analysis, conversation

| # | Ask | Must show |
|---|-----|-----------|
| C1 | Frank Jr ka CRM record dikhao. | Both exact matches: Lead (Cape,s, marcellecaples@yahoo.com, 12143923587, New) and Contact (marcellecaples@yahoo.com, 2143923587, Contact). |
| C2 | Frank Jr ki CRM analysis do. | CRM AI analysis labelled as the CRM's own output with Verified/placeholder honesty, or an honest backend-failure note — never a timeout blamed on the CRM when the CRM returned a 500. |
| C3 | Frank Jr ke dono records ki analysis do. | BOTH analyses in one answer (parallel tool). One record's failure must not take the other down. |
| C4 | Frank Jr ka WhatsApp thread dikhao. | Thread bodies in order, newest first, with direction (Inbound/Outbound). |
| C5 | Frank Jr ki contact details dikhao. | Both records' email/phone/company/status, no record_id hex. |
| C6 | Frank Jr ka latest (taaza) CRM data dikhao. | Fresh 360 for both records (fresh=true), with timeline counts and last interaction. |

## Queue, briefing, deals, history

| # | Ask | Must show |
|---|-----|-----------|
| H1 | WhatsApp par kon wait kar raha hai? | Queue from the latest agent run, dated with as_of; 0 on WhatsApp is a valid answer only if the queue really holds none. |
| H2 | Aaj ki briefing do. | Yesterday's CRM payments, who is waiting, biggest stalled deals. |
| H3 | Which deals are stalled? | Stalled deals (CRM Deal Watch definition for deals, not ERP invoice balances relabelled). |
| H4 | Sab se bara stalled deal konsa hai? | T shirts — Ibrahim — $8,965,478 — 110 days idle (baseline Oct 27, 2026; re-check age on the run day). |
| H5 | What did we discuss last time? | Recent conversation titles with updated dates and turn counts — must NOT collapse to "I couldn't verify that figure". Root cause fixed Oct 27: recall results were excluded from the guard's evidence, so their own dates failed validation. |
| H6 | Compare the last two lead pulls. | Added/removed/changed counts from compare_lead_pulls, or a plain statement that historical pulls are not retained — never a bare "couldn't verify". |

## Dashboard / revenue

| # | Ask | Must show |
|---|-----|-----------|
| D1 | Give me today's revenue. | Revenue today + month to date + payments today count, matching the dashboard tile for the ERP business day. |
| D2 | What is month to date revenue? | Same month-to-date figure as D1. |
| D3 | yes export the data to CSV (after a Frank Jr answer) | CSV text in chat with the two Frank Jr records. There is NO downloadable-file export in chat: the answer must print the CSV text, never promise an attachment it cannot produce. |

## Known limits (not bugs to "fix" by inventing data)

- CRM analysis is an uncached CRM-side AI call: on a cold backend it can
  take ~49s and the CRM itself returns 500s at times. The parallel tool
  and per-record failure reporting are the mitigation; a CRM 500 is the
  CRM's failure, and the answer must say so.
- "Stalled orders" from the ERP is documented as an unreliable signal
  (no order status timestamps): it must never be presented as a proven
  production delay.
- MJ BizCon Las Vegas 2025 leads count returned 0 on Oct 27: verify
  against the leads source field before calling it wrong — an event
  name is not a lead field, so 0 may be correct.
- Liza John January 2026 sales (quotes 41 / $439,258.20, orders 37 /
  $377,653, payments 9 / $320,339, reported Oct 27) is a baseline to
  re-verify with the rep summary tool, not a memorised fact.
