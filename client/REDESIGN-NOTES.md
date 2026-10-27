# Biztek AI Operations Dashboard — Front-End Redesign Notes

## 1. The Protected Zone — All 14 Frozen Orb Files

In strict compliance with the core protection rules, all 14 files responsible for the orb, the central core orb, and the turn execution lifecycle state machine remain completely untouched. 

Below is the verified `git diff --stat` output for every one of the 14 files:

| File Path | Description | git diff --stat Output |
| :--- | :--- | :--- |
| `client/components/command-center/jarvis-orb.tsx` | Canvas orb renderer, 6 color fields, RAF loop, halftone screen | `0 diff (clean)` |
| `client/components/command-center/command-center.tsx` | Core orb host & hero orb instantiation | `0 diff (clean)` |
| `client/components/command-center/execution-context.tsx` | Turn lifecycle state machine (idle → starting → running → synthesizing → completed) | `0 diff (clean)` |
| `client/components/command-center/execution-overlay.tsx` | Real-time lifecycle overlay & transition phases | `0 diff (clean)` |
| `client/components/command-center/neural-network.tsx` | Orbital tracks (`jn-orbit`), specialist network nodes & core orb socket | `0 diff (clean)` |
| `client/components/command-center/neural-backdrop.tsx` | Orb canvas backdrop visual effect | `0 diff (clean)` |
| `client/components/command-center/operation-flow.tsx` | Routing tree & mini orb indicator | `0 diff (clean)` |
| `client/components/command-center/operation-panel.tsx` | Operation lifecycle status, step timers & tone | `0 diff (clean)` |
| `client/components/command-center/activity-timeline.tsx` | Turn activity log and timeline events | `0 diff (clean)` |
| `client/components/command-center/history-drawer.tsx` | Historical replay of execution lifecycle | `0 diff (clean)` |
| `client/lib/task-events.ts` | Execution state derivation & event reduction | `0 diff (clean)` |
| `client/lib/presentation.ts` | `deriveOrbState`, `OrbState`, `ORB_CAPTION` | `0 diff (clean)` |
| `client/hooks/use-task-events.ts` | Event stream hook driving turn lifecycle | `0 diff (clean)` |
| `client/hooks/use-reduced-motion.ts` | Accessibility reduced motion detection hook | `0 diff (clean)` |

**Verification Command Output:**
```bash
$ git diff --stat \
    client/components/command-center/jarvis-orb.tsx \
    client/components/command-center/command-center.tsx \
    client/components/command-center/execution-context.tsx \
    client/components/command-center/execution-overlay.tsx \
    client/components/command-center/neural-network.tsx \
    client/components/command-center/neural-backdrop.tsx \
    client/components/command-center/operation-flow.tsx \
    client/components/command-center/operation-panel.tsx \
    client/components/command-center/activity-timeline.tsx \
    client/components/command-center/history-drawer.tsx \
    client/lib/task-events.ts \
    client/lib/presentation.ts \
    client/hooks/use-task-events.ts \
    client/hooks/use-reduced-motion.ts
# (Zero diff output — 0 files changed)
```

---

## 2. Per-Page Summary: Layout, Components, and Motion

1. **Command Center (`client/app/page.tsx` + `client/components/chat-interface.tsx`)**:
   - **Layout**: Rebalanced into a dedicated 2-column operations stage (`minmax(0, 1.25fr)` for the neural network stage and `minmax(440px, 1fr)` for the chat column).
   - **Components**: Redesigned composer states (idle, sending, queued, reconnecting), lime-accented user speech bubbles, elevated assistant cards with provenance chips, and redesigned history panel.
   - **Motion**: Subtle 200ms message entrance staggers, smooth stop/send micro-transitions, and collapse under `prefers-reduced-motion`.

2. **Overview (`client/components/dashboard/overview-panel.tsx` + `client/components/charts.tsx`)**:
   - **Layout**: High-density 3×3 KPI matrix with bold tabular numerics and explicit period/context captions, followed by 14-day trend charts and top reps.
   - **Components**: Polished `KpiCard` tiles, custom-themed ECharts for 14-day revenue and quote volume with formatted currency axes, and rep payment counts.
   - **Motion**: 300ms chart rendering animations settling precisely on backend numbers, with gentle hover tooltips.

3. **Receivables (`client/components/dashboard/unpaid-table.tsx` + `client/components/dashboard/panels.tsx`)**:
   - **Layout**: Table shell with sticky headers, "Showing 25 of 401" pill framing, and full horizontal overflow handling for responsive views.
   - **Components**: High-contrast tabular balance column, warning-tinted invoice age (>60d), distinct `OVERDUE` (red badge) vs `WITHIN TERMS` (surface badge) pills, and "Overdue after 30 days" functional threshold dropdown.
   - **Motion**: 150ms table row hover transitions and animated "Show 25 more" pagination expansion.

4. **Pipeline (`client/components/dashboard/stale-quote-table.tsx` + `client/components/dashboard/stalled-order-table.tsx`)**:
   - **Layout**: Two vertically stacked data tables for Stale Quotes and Stalled Orders with preserved header explanations.
   - **Components**: Amber pill badges for days stale, tabular amounts, and prominent ERP unreliability warning banner on stalled orders (`reliable: false`).
   - **Motion**: Micro-interaction hover states and smooth filter threshold updates without page reloads.

5. **Leads (`client/components/dashboard/stuck-leads-table.tsx`)**:
   - **Layout**: Responsive table with clear column hierarchy and explicit missing-field chips.
   - **Components**: Instantly scannable missing-field chips (`quantity`, `size`, `material`, `finish`), artwork status badges (`Provided`, `Missing`, `Not stated`), and honest fallback `"—"` for missing companies and `"Name not provided"` for missing contacts.
   - **Motion**: Filter bar transitions and subtle hover highlights on lead rows.

6. **AI Review (`client/components/approval-inbox.tsx`)**:
   - **Layout**: High-impact operational queue with prominent read-only warning banner, run stats header, executive summary, priorities, CRM digest, and filterable recommendations.
   - **Components**: Autonomous dispatch badge, lime "Run agents" button, per-agent breakdown pills, filter tabs (`All 50`, `Negotiation`, `Collections`, etc.), draft message cards, and working `Copy draft` (with "Copied" feedback) and `Mark reviewed` actions.
   - **Motion**: 200ms clipboard copied pulse, tab switching transitions, and spin animation on manual refresh.

7. **Chats (`client/components/chat-history.tsx`)**:
   - **Layout**: Stored conversations list with in-place popup viewer modal for seamless historical inspection.
   - **Components**: Active thread indicator (`" · open now"`), 2-click delete confirmation (`"Delete"` → `"Confirm delete"`), modal navigation (`Previous chat` / `Next chat`), and `"Continue this chat"` handover to Command Center.
   - **Motion**: Backdrop fade-in, smooth dialog scale entrance, and arrow-key walkthrough animations.

8. **Customer Context Drawer (`client/components/customer-context.tsx`)**:
   - **Layout**: Slide-out overlay drawer triggered via "View context" links or manual numeric lookup.
   - **Components**: Numeric ID input with `#` prefix and lime `Load` button, customer summary cards (Quotes, Orders, Payments), lead match badges (`Matched`, `Matched by email`, `Ambiguous`, `New customer`), and backend ambiguity warning callouts.
   - **Motion**: Smooth sheet slide-in from right edge, skeleton pulse while loading, and instant key-binding (Escape) dismissal.

---

## 3. Design Tokens Architecture

All design tokens are centralized in [`client/app/globals.css`](file:///home/abdullah-jutt/Documents/BiztekAI/Ai-Dashboard/client/app/globals.css) and defined using Tailwind CSS `@theme` variables:

- **Surfaces**:
  - `--background`: Deep obsidian canvas `#07090d`
  - `--surface`: Layer 1 elevated card surface `#0e121a`
  - `--surface-raised`: Layer 2 elevated component surface `#151b26`
  - `--border`: Semantic hairline border `rgba(255, 255, 255, 0.08)`
  - `--border-subtle`: Subtle divider `rgba(255, 255, 255, 0.04)`
- **Accents**:
  - `--accent`: Brand electric lime `#a3e635` (used for primary actions, active indicators, and focus states)
  - `--accent-hover`: `#bbf451`
  - `--accent-foreground`: High-contrast dark text `#0d1404`
- **Semantic Feedback**:
  - `--positive`: Emerald `#34d399` (`--positive-muted`: `rgba(52, 211, 153, 0.12)`)
  - `--warning`: Amber `#fbbf24` (`--warning-muted`: `rgba(251, 191, 36, 0.12)`)
  - `--negative`: Coral red `#f87171` (`--negative-muted`: `rgba(248, 113, 113, 0.12)`)
- **Typography & Font Scale**:
  - Monospace font family: `JetBrains Mono, ui-monospace, SFMono-Regular, monospace` for tabular numbers, IDs, codes, and balances.
  - Sans-serif font family: `Inter, -apple-system, BlinkMacSystemFont, sans-serif`.
  - Type scale: `text-xs` (12px), `text-sm` (14px), `text-base` (16px), `text-lg` (18px), `text-2xl` (24px).
- **Motion Durations**:
  - Fast micro-interactions: 150ms (`--duration-fast`)
  - Component transitions: 200ms (`--duration-normal`)
  - Panel & drawer entrances: 300ms (`--duration-slow`)
  - Collapses to `0ms` under `@media (prefers-reduced-motion: reduce)`.

---

## 4. Logo Implementation

- **Asset**: Located at [`client/public/biztek-logo.png`](file:///home/abdullah-jutt/Documents/BiztekAI/Ai-Dashboard/client/public/biztek-logo.png).
- **Component**: [`client/components/brand-logo.tsx`](file:///home/abdullah-jutt/Documents/BiztekAI/Ai-Dashboard/client/components/brand-logo.tsx).
- **Wiring**: Next.js `<Image priority src="/biztek-logo.png" />` is wired directly into the global header.
- **Honest Fallback**: If the image fails to load or is unavailable, `BrandLogo` automatically renders an accessible styled SVG mark `[B] BIZTEK AI | Command Center` so the header navigation never breaks.

---

## 5. Deliberate Assumptions & Preserved Behaviors

1. **Read-Only Invariants**:
   - The queue in AI Review does not have an approval/rejection backend endpoint (`/api/approve` returns 404). As designed by the backend, "Mark reviewed" only updates the local browser session state.
2. **ERP Stalled Order Timestamps**:
   - The ERP does not record order status timestamps; stalled order rows are derived from invoice age only. The warning callout was deliberately preserved word-for-word and placed prominently.
3. **Overdue Days Threshold**:
   - Changing the "Overdue after X days" dropdown filters which rows receive the `OVERDUE` badge, not which rows are returned in the table. This is intentional backend behavior and preserved exactly.
4. **Voice Console**:
   - Voice mode remains disabled because `POST /api/voice` returns 404 on the backend. The UI clearly states "Voice is not connected" and text chat is fully operational.
5. **Customer ID Numeric Lookup**:
   - The backend does not expose a customer search endpoint; customer context requires looking up by numeric ID. The explanation is kept visible on the drawer.

---

## 6. Verification Results

| Verification Category | Check Command | Result | Notes |
| :--- | :--- | :--- | :--- |
| **Protected Zone** | `git diff --stat <14 orb files>` | **PASS** | 0 diffs across all 14 files |
| **Backend Boundary** | `git status` on `server/` | **PASS** | 0 files modified in backend |
| **TypeScript Check** | `npx tsc --noEmit` from `client/` | **PASS** | 0 errors |
| **Linting Check** | `npx eslint` across changed files | **PASS** | 0 errors, 0 warnings |
| **Test Suite** | `npx vitest run` (17 test files) | **PASS** | 181 / 181 tests passed |
| **Production Build** | `npm run build` | **PASS** | Turbopack static & dynamic routes generated |
| **Visual QA** | 14 screenshots captured | **PASS** | Desktop & tablet viewports verified |
| **Behavior Checks** | Recommendation tabs, pagination, drawers | **PASS** | All interactive states verified in headless Chrome |
| **Motion Check** | `prefers-reduced-motion` emulation | **PASS** | Animations cleanly collapse |

---

## 7. What Was Intentionally NOT Redesigned

- **The Orb**: Core canvas renderer, halftone shader, 6 color energy fields, and turn execution lifecycle state machine remain completely frozen in code, styles, and animation timing.
- **Backend Endpoints & API Contracts**: Zero changes were made to FastAPI routes, schemas, or server logic.
- **Tab Routes & Names**: Exactly `Command Center`, `Overview`, `Receivables`, `Pipeline`, `Leads`, `AI review`, `Chats`.
- **Numbers & Data Fields**: Every figure rendered comes strictly from backend responses; no numbers were faked, rounded, or mocked.
