// Types mirror the Pydantic / JSON models served by the FastAPI backend
// (server/app/schemas/*.py and server/app/services/metrics.py).

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
  reliable: boolean;
}

export interface StalledOrderList {
  reliable: boolean;
  items: StalledOrder[];
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
  stalled_orders: StalledOrderList;
  summary: SignalsSummary;
}

export interface SignalsQuery {
  staleDays?: number;
  stalledDays?: number;
  overdueDays?: number;
}

// GET /api/metrics and the `snapshot` event from GET /api/live
// (server/app/services/metrics.py: build_snapshot_from_docs)
export interface RepMetric {
  rep_id: number;
  name: string;
  total: number;
  count: number;
  total_display: string;
}

export interface QuotesPerDay {
  date: string;
  count: number;
}

export interface RevenuePerDay {
  date: string;
  total: number;
  total_display: string;
}

export interface ScannedCounts {
  quotes: number;
  orders: number;
  payments: number;
}

export interface DashboardSnapshot {
  generated_at: string;
  revenue_today: number;
  revenue_today_display: string;
  revenue_month_to_date: number;
  revenue_month_to_date_display: string;
  revenue_last_7_days: number;
  revenue_last_7_days_display: string;
  payments_count_today: number;
  open_quotes_count: number;
  open_quotes_value: number;
  open_quotes_value_display: string;
  unpaid_total: number;
  unpaid_count: number;
  unpaid_total_display: string;
  overdue_total: number;
  overdue_count: number;
  overdue_total_display: string;
  top_reps_this_month: RepMetric[];
  quotes_per_day: QuotesPerDay[];
  revenue_per_day: RevenuePerDay[];
  scanned: ScannedCounts;
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

// GET /health (server/app/main.py)
export interface HealthResponse {
  status: "ok" | "degraded";
  mongo: boolean;
  database: string;
  scheduler: boolean;
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

// Sent when provisional streamed text must be discarded (for example the model
// started answering and then decided to call a tool instead).
export interface ChatResetEvent {
  reason?: string;
}

export type ChatStreamEvent =
  | { event: "tool"; data: ChatToolEvent }
  | { event: "token"; data: ChatTokenEvent }
  | { event: "reset"; data: ChatResetEvent }
  | { event: "done"; data: ChatDoneEvent }
  | { event: "error"; data: ChatErrorEvent };

export type AgentConfidence = "low" | "medium" | "high";

export interface NegotiationOutput {
  kind?: "negotiation";
  input_hash?: string;
  customer_id?: number | null;
  customer_name?: string | null;
  confidence?: AgentConfidence;
  evidence?: string;
  quote_id?: number;
  quote_amount?: number;
  likely_reason?:
    | "price"
    | "no_response"
    | "new_customer_no_history"
    | "repeat_customer_slow"
    | "unclear";
  tactic?: "follow_up" | "adjust_price" | "bundle_offer" | "let_go";
  message_draft?: string | null;
  flags?: string[];
  source?: "agent" | "cache";
  rejected_reason?: string;
  error?: string;
}

export interface CollectionsOutput {
  kind?: "collections";
  input_hash?: string;
  customer_id?: number | null;
  customer_name?: string | null;
  confidence?: AgentConfidence;
  evidence?: string;
  invoice_id?: number;
  balance?: number;
  approach?: "soft_reminder" | "firm_nudge" | "hold_off" | "escalate_to_human";
  reason?: string;
  message_draft?: string | null;
  flags?: string[];
  source?: "agent" | "cache";
  rejected_reason?: string;
  error?: string;
}

export interface AgentPriority {
  customer_name: string;
  why: string;
  suggested_action: string;
}

export interface MasterBriefing {
  bullets: string[];
  priorities: AgentPriority[];
  conflicts_resolved: string[];
  flags?: string[];
  rejected_reason?: string;
  error?: string;
}

export interface AgentRunCounts {
  negotiation_total: number;
  collections_total: number;
  unclear: number;
  needs_review: number;
  cached: number;
}

export interface AgentUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface AgentRunStarted {
  run_id: string;
  status: "started";
}

export interface AgentRunBriefing {
  run_id: string;
  started_at: string;
  finished_at: string;
  finished_epoch?: number;
  counts: AgentRunCounts;
  negotiation: NegotiationOutput[];
  collections: CollectionsOutput[];
  master: MasterBriefing | null;
  usage: AgentUsage | null;
  errors: string[];
}

export interface EmptyAgentBriefing {
  run_id: null;
  message: string;
}

export type LatestBriefingResponse = AgentRunBriefing | EmptyAgentBriefing;

// SSE events from GET /api/live (server/app/api/live.py + scheduler.py)
export interface LiveErrorPayload {
  message?: string;
  trigger?: string;
  run_id?: string;
  error?: string;
}

export type LiveStreamEvent =
  | { event: "snapshot"; data: DashboardSnapshot }
  | { event: "briefing"; data: AgentRunBriefing | null }
  | { event: "error"; data: LiveErrorPayload };

export type LiveConnectionStatus =
  | "connecting"
  | "live"
  | "reconnecting"
  | "offline";
