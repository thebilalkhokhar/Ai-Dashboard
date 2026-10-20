export interface StaleQuoteSummary {
  count: number;
  total_amount: number;
}

export interface UnpaidSummary {
  count: number;
  total_amount: number;
  overdue_count: number;
  unpaid_count: number;
  partially_paid_count: number;
}

export interface StalledOrderSummary {
  count: number;
  total_amount: number;
}

export interface SignalsSummary {
  quotes_scanned: number;
  orders_scanned: number;
  data_from: string;
  data_to: string;
  stale_quotes: StaleQuoteSummary;
  unpaid_invoices: UnpaidSummary;
  stalled_orders: StalledOrderSummary;
}

export interface SignalsReport {
  generated_at: string;
  stale_quotes: Record<string, unknown>[];
  unpaid_invoices: Record<string, unknown>[];
  stalled_orders: Record<string, unknown>[];
  summary: SignalsSummary;
}

export interface CustomerContext {
  [key: string]: unknown;
}
