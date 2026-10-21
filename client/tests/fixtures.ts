// Fixture data for the client tests: the same shapes the FastAPI backend sends.

import type {
  DashboardSnapshot,
  SignalsReport,
  StaleQuote,
  StalledOrder,
  StuckLead,
  StuckLeadsResponse,
  UnpaidInvoice,
} from "@/types/api";

export const customer = { id: 3312, company_name: "Polkadot" };
export const rep = { id: 119, name: "Erick Dean" };

export function staleQuote(overrides: Partial<StaleQuote> = {}): StaleQuote {
  return {
    id: 20791,
    ref: "20021-2026-10",
    status: "Estimate",
    status_order: 5,
    amount: 56600,
    age_days: 4.2,
    days_stale: 4,
    created_at: "2026-10-16T15:25:33-05:00",
    customer,
    sale_rep: rep,
    ...overrides,
  };
}

export function unpaidInvoice(overrides: Partial<UnpaidInvoice> = {}): UnpaidInvoice {
  return {
    id: 27608,
    inv_ref: "27608-2025-10",
    status: 2,
    status_label: "Approved",
    payment_status: 0,
    payment_status_label: "Not Paid",
    total_amount: 357500,
    amount_paid: 0,
    balance: 357500,
    overdue: true,
    days_invoice_age: 356,
    invoice_date: "2025-10-30T06:56:39-05:00",
    customer: { id: 2207, company_name: "Pure 7" },
    sale_rep: rep,
    ...overrides,
  };
}

export function stalledOrder(overrides: Partial<StalledOrder> = {}): StalledOrder {
  return {
    id: 33245,
    inv_ref: "33245-2026-10",
    status: 2,
    status_label: "Approved",
    payment_status: 0,
    payment_status_label: "Not Paid",
    invoice_date: "2026-10-01T09:00:00-05:00",
    days_invoice_age: 20,
    age_days: 20.5,
    total_amount: 70425,
    customer,
    sale_rep: rep,
    ...overrides,
  };
}

export function stuckLead(overrides: Partial<StuckLead> = {}): StuckLead {
  return {
    lead_id: "L-1",
    contact_name: "Keiwan Bentley",
    company: "Zaskii Boyz",
    status: "New",
    source: "Ecommerce",
    days_since_activity: 3,
    last_activity_at: "2026-10-18T10:00:00-05:00",
    created_at: "2026-10-15T10:00:00-05:00",
    product: "mylar bag",
    type: null,
    quantity: "500",
    size: null,
    material: null,
    finish: null,
    artwork_present: false,
    description_shape: "json",
    missing: ["artwork", "size", "material", "finish"],
    has_contact_details: true,
    ...overrides,
  };
}

export function signalsReport(overrides: Partial<SignalsReport> = {}): SignalsReport {
  return {
    generated_at: "2026-10-21T15:00:00+00:00",
    stale_quotes: [staleQuote()],
    unpaid_invoices: [unpaidInvoice()],
    stalled_orders: { reliable: false, items: [stalledOrder()] },
    summary: {
      quotes_scanned: 7389,
      orders_scanned: 3411,
      data_from: "2025-04-03T08:16:39+00:00",
      data_to: "2026-10-20T22:43:10+00:00",
      stale_quotes: { count: 311, total_amount: 987574.61 },
      unpaid_invoices: {
        count: 363,
        total_amount: 2344607.49,
        overdue_count: 238,
        unpaid_count: 278,
        partially_paid_count: 85,
      },
      stalled_orders: { count: 3370, total_amount: 26635251.69, reliable: false },
      item_limit: 200,
    },
    ...overrides,
  };
}

export function emptySignalsReport(): SignalsReport {
  return signalsReport({
    stale_quotes: [],
    unpaid_invoices: [],
    stalled_orders: { reliable: false, items: [] },
    summary: {
      ...signalsReport().summary,
      stale_quotes: { count: 0, total_amount: 0 },
      unpaid_invoices: {
        count: 0,
        total_amount: 0,
        overdue_count: 0,
        unpaid_count: 0,
        partially_paid_count: 0,
      },
      stalled_orders: { count: 0, total_amount: 0, reliable: false },
    },
  });
}

export function snapshotFixture(overrides: Partial<DashboardSnapshot> = {}): DashboardSnapshot {
  return {
    generated_at: "2026-10-21T15:00:00+00:00",
    revenue_today: 410,
    revenue_today_display: "$410",
    revenue_month_to_date: 128400,
    revenue_month_to_date_display: "$128,400",
    revenue_last_7_days: 96200,
    revenue_last_7_days_display: "$96,200",
    payments_count_today: 4,
    open_quotes_count: 311,
    open_quotes_value: 987574.61,
    open_quotes_value_display: "$987,574",
    unpaid_total: 2344607.49,
    unpaid_count: 363,
    unpaid_total_display: "$2,344,607",
    overdue_total: 1810233.1,
    overdue_count: 238,
    overdue_total_display: "$1,810,233",
    top_reps_this_month: [
      { rep_id: 119, name: "Erick Dean", total: 88000, count: 30, total_display: "$88,000" },
    ],
    quotes_per_day: [{ date: "2026-10-20", count: 12 }],
    revenue_per_day: [{ date: "2026-10-20", total: 410, total_display: "$410" }],
    scanned: { quotes: 7389, orders: 3411, payments: 902 },
    ...overrides,
  };
}

export function stuckLeadsFixture(overrides: Partial<StuckLeadsResponse> = {}): StuckLeadsResponse {
  return { generated_at: "2026-10-21T15:00:00+00:00", days: 14, total: 1, items: [stuckLead()], ...overrides };
}

export function emptyStuckLeads(): StuckLeadsResponse {
  return { generated_at: "2026-10-21T15:00:00+00:00", days: 14, total: 0, items: [] };
}
