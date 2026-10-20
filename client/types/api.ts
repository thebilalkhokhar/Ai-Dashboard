export interface SaleRep {
  id: number | null;
  name: string | null;
}

export interface CustomerRef {
  id: number | null;
  company_name: string | null;
}

export interface StaleQuote {
  id: number | null;
  ref: string | null;
  status: string | null;
  status_order: number | null;
  amount: number;
  age_days: number | null;
  days_stale: number;
  created_at: string | null;
  customer: CustomerRef | null;
  sale_rep: SaleRep | null;
}

export interface UnpaidInvoice {
  id: number | null;
  inv_ref: string | null;
  status: number | null;
  status_label: string | null;
  payment_status: number | null;
  payment_status_label: string | null;
  total_amount: number;
  amount_paid: number;
  balance: number;
  overdue: boolean;
  days_invoice_age: number;
  invoice_date: string | null;
  customer: CustomerRef | null;
  sale_rep: SaleRep | null;
}

export interface StalledOrder {
  id: number | null;
  inv_ref: string | null;
  status: number | null;
  status_label: string | null;
  payment_status: number | null;
  payment_status_label: string | null;
  invoice_date: string | null;
  days_invoice_age: number;
  age_days: number | null;
  total_amount: number;
  customer: CustomerRef | null;
  sale_rep: SaleRep | null;
}

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
  data_from: string | null;
  data_to: string | null;
  stale_quotes: StaleQuoteSummary;
  unpaid_invoices: UnpaidSummary;
  stalled_orders: StalledOrderSummary;
}

export interface SignalsReport {
  generated_at: string;
  stale_quotes: StaleQuote[];
  unpaid_invoices: UnpaidInvoice[];
  stalled_orders: StalledOrder[];
  summary: SignalsSummary;
}

export interface Contact {
  email: string | null;
  phone: string | null;
}

export interface QuoteSummary {
  count: number;
  total_amount: number;
  status_counts: Record<string, number>;
  first_at: string | null;
  last_at: string | null;
}

export interface OrderSummary extends QuoteSummary {
  amount_paid: number;
  balance: number;
  unpaid_count: number;
  partially_paid_count: number;
  fully_paid_count: number;
  payment_status_counts: Record<string, number>;
}

export interface PaymentSummary {
  count: number;
  total_amount: number;
  first_at: string | null;
  last_at: string | null;
}

export interface LeadOutcome {
  lead_ref: string | null;
  matched: boolean | null;
  matched_by: string | null;
  ambiguous: boolean;
  customer_id: number | null;
  customer_name: string | null;
  is_new_customer: boolean | null;
  quotes_since_lead: Record<string, unknown>;
  orders_since_lead: Record<string, unknown>;
  payments_since_lead: Record<string, unknown>;
}

export interface CustomerContext {
  customer_id: number;
  company_name: string;
  contact: Contact;
  quotes: QuoteSummary;
  orders: OrderSummary;
  payments: PaymentSummary;
  leads: LeadOutcome[];
  notes: string[];
}

export interface HealthResponse {
  status: "ok" | "degraded";
  mongo: boolean;
  database: string;
}

export interface ChatRequest {
  thread_id: string;
  message: string;
}

export interface ChatToolEvent {
  name: string;
}

export interface ChatTokenEvent {
  text: string;
}

export interface ChatDoneEvent {
  text: string;
  thread_id: string;
}

export interface ChatErrorEvent {
  message: string;
}

export type ChatStreamEvent =
  | { event: "tool"; data: ChatToolEvent }
  | { event: "token"; data: ChatTokenEvent }
  | { event: "done"; data: ChatDoneEvent }
  | { event: "error"; data: ChatErrorEvent };
