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
  // maximum rows per list in the report; the counts above always cover
  // everything. Absent on reports saved before the cap existed.
  item_limit?: number | null;
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
  includeStalled?: boolean;
}

// GET /api/leads/stuck (server/app/schemas/signals.py: StuckLead)
// Parsed entirely by backend Python. artwork_present is null when the lead
// description never stated it - the backend never guesses.
export type LeadMissingField =
  | "artwork"
  | "quantity"
  | "size"
  | "material"
  | "finish"
  | "contact_info";

export interface StuckLead {
  lead_id: string;
  contact_name: string | null;
  company: string | null;
  status: string;
  source: string | null;
  days_since_activity: number;
  last_activity_at: string | null;
  created_at: string | null;
  product: string | null;
  type: string | null;
  quantity: string | null;
  size: string | null;
  material: string | null;
  finish: string | null;
  artwork_present: boolean | null;
  description_shape: string | null;
  missing: LeadMissingField[];
  has_contact_details: boolean;
}

export interface StuckLeadsResponse {
  generated_at: string;
  days: number;
  total: number;
  items: StuckLead[];
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

// Sent after a tool succeeds: the pagination and aggregate facts the backend
// actually computed for that call. Every field is optional because a tool only
// reports what it really knows - the UI must not imply more.
export interface ChatToolResultEvent {
  tool: string;
  records_total?: number;
  records_returned?: number;
  records_remaining?: number | null;
  complete?: boolean;
  // a count the aggregate tool itself computed (get_revenue's payment_count)
  record_count?: number;
  money_label?: string;
  money_total?: number | null;
  money_covers?: string;
  window?: { date_from?: string | null; date_to?: string | null };
}

export interface ChatTokenEvent {
  text: string;
}

export interface ChatDoneEvent {
  text: string;
  thread_id: string;
  // the task id this turn's events are recorded under (announced by the first
  // `task` event); absent on a backend that predates task correlation
  task_id?: string;
}

export interface ChatErrorEvent {
  message: string;
}

// First frame of a chat turn: the id the backend records this turn's real
// task events under, so the UI can watch that stream while it works.
export interface ChatTaskEvent {
  task_id: string;
  thread_id: string;
}

// Sent when provisional streamed text must be discarded (for example the model
// started answering and then decided to call a tool instead).
export interface ChatResetEvent {
  reason?: string;
}

export type ChatStreamEvent =
  | { event: "task"; data: ChatTaskEvent }
  | { event: "tool"; data: ChatToolEvent }
  | { event: "tool_result"; data: ChatToolResultEvent }
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
  // runs saved before the customer-facing reminder existed can still say
  // "escalate_to_human"; new runs keep a reminder draft plus an escalation note
  approach?: "soft_reminder" | "firm_nudge" | "hold_off" | "escalate_to_human";
  tone?: "gentle" | "standard" | "firm";
  reason?: string;
  message_draft?: string | null;
  escalation_note?: string | null;
  flags?: string[];
  source?: "agent" | "cache";
  rejected_reason?: string;
  error?: string;
}

// Sub-agent 3 output (server/app/schemas/agents.py: LeadFollowupOutput).
// Read-only: drafts are stored text for a human rep, nothing is sent.
export interface LeadFollowupOutput {
  kind?: "lead_followup";
  input_hash?: string;
  lead_id?: string;
  contact_name?: string | null;
  company?: string | null;
  product?: string | null;
  missing?: LeadMissingField[];
  blocker?: string;
  owner_team?: "design" | "sales" | "unclear";
  action_plan?: string;
  message_draft?: string | null;
  status?: string;
  days_since_activity?: number;
  note?: string | null;
  confidence?: AgentConfidence;
  evidence?: string;
  flags?: string[];
  source?: "agent" | "cache";
  rejected_reason?: string;
  error?: string;
}

// Sub-agent 4 output (server/app/schemas/agents.py: CrmEngagementOutput).
// One Zoho CRM record whose last interaction came from the customer.
// Identity fields are filled by the pipeline, never by the model.
export interface CrmEngagementOutput {
  kind?: "crm_engagement";
  input_hash?: string;
  record_id?: string;
  record_type?: string;
  contact_name?: string | null;
  company?: string | null;
  waiting_days?: number;
  situation?: string;
  action_plan?: string;
  message_draft?: string | null;
  confidence?: AgentConfidence;
  evidence?: string;
  flags?: string[];
  source?: "agent" | "cache";
  rejected_reason?: string;
  error?: string;
}

// Sub-agent 5 output (server/app/schemas/agents.py: CrmDealWatchOutput).
// One stalled Zoho CRM project or estimate from the synced module rows.
export interface CrmDealWatchOutput {
  kind?: "crm_deal_watch";
  input_hash?: string;
  record_id?: string;
  deal_kind?: string;
  deal_name?: string | null;
  account_name?: string | null;
  amount?: number;
  days_idle?: number;
  stall_reason?: string;
  action_plan?: string;
  message_draft?: string | null;
  confidence?: AgentConfidence;
  evidence?: string;
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
  // set when the model's brief failed validation and code wrote the brief
  generated_by?: "code_template";
  error?: string;
}

export interface AgentRunCounts {
  negotiation_total: number;
  collections_total: number;
  // optional: briefings saved before the lead follow-up agent existed
  lead_followup_total?: number;
  // optional: briefings saved before the CRM agents existed
  crm_engagement_total?: number;
  crm_deal_watch_total?: number;
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
  // optional: briefings saved before the lead follow-up agent existed
  lead_followup?: LeadFollowupOutput[];
  // optional: briefings saved before the CRM agents existed
  crm_engagement?: CrmEngagementOutput[];
  crm_deal_watch?: CrmDealWatchOutput[];
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

// ---------------------------------------------------------------- task events
// These mirror server/app/schemas/events.py and the registry/service endpoints.
// They are the only source of agent/task state in the Command Center: nothing
// here is synthesised on the client.

export type TaskEventType =
  | "task.created"
  | "task.started"
  | "task.completed"
  | "task.failed"
  | "task.cancelled"
  | "agent.queued"
  | "agent.started"
  | "agent.progress"
  | "agent.completed"
  | "agent.failed"
  | "tool.started"
  | "tool.completed"
  | "erp.sync_started"
  | "erp.sync_completed"
  | "erp.sync_failed"
  | "data.filter_completed"
  | "data.aggregation_completed"
  | "chat.validation_started"
  | "chat.validation_completed"
  | "chat.response_started";

export interface TaskEventPayload {
  // agent.progress uses cached=true (boolean); agent.completed reports the
  // number of cached items, so `cached` is genuinely both depending on the type.
  cached?: boolean | number;
  status?: string;
  index?: number;
  tool?: string;
  ok?: boolean;
  error?: string;
  duration_ms?: number;
  total?: number;
  fresh?: number;
  processed?: number;
  failed?: number;
  needs_review?: number;
  generated_by?: string;
  counts?: Record<string, number>;
  total_tokens?: number;
  [key: string]: unknown;
}

export interface TaskEvent {
  v: number;
  event_id: string;
  task_id: string;
  seq: number;
  ts: string;
  type: TaskEventType;
  agent_id: string | null;
  payload: TaskEventPayload;
}

// GET /api/agents (server/app/agents/registry.py)
export type AgentKind = "orchestrator" | "specialist" | "assistant";

// The status the registry derives from persisted events; `idle` means no agent
// event has ever been recorded, never "working".
export type RegistryAgentStatus =
  | "idle"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "unknown";

export interface RegistryAgent {
  id: string;
  name: string;
  kind: AgentKind;
  purpose: string;
  tools: string[];
  enabled: boolean;
  status: RegistryAgentStatus;
  status_source: "events" | "none";
}

export interface AgentsResponse {
  agents: RegistryAgent[];
}

// GET /api/tasks (server/app/services/events.py: recent_tasks)
// "unknown" is a real backend answer: the task has no terminal event and its
// newest one is too old to call it live. The UI must not paint that as running.
export type TaskListStatus =
  | "created"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "unknown";

export interface TaskSummary {
  task_id: string;
  last_seq: number;
  last_type: TaskEventType;
  status: TaskListStatus;
  ts: string | null;
  // the user's own request, recorded on task.created (chat tasks only)
  prompt?: string | null;
  // what started a pipeline task ("scheduler", "manual", ...)
  trigger?: string | null;
  // all four below come from the task's own stored events; null means the
  // events never recorded it, and the UI must not fill the gap
  created_at?: string | null;
  updated_at?: string | null;
  duration_ms?: number | null;
  agents?: string[];
  // exception type name from a failed terminal event, never its message
  error_kind?: string | null;
}

export interface TasksResponse {
  tasks: TaskSummary[];
}
