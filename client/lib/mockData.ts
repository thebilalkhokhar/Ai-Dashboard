export const users = [
  {
    id: 134,
    name: "Harry Brook",
    zoho_id: "6374650000021728001",
    is_active: 1,
    company_id: 1,
    e_com_user: 1,
  },
  {
    id: 84,
    name: "Jessica Joe",
    zoho_id: "6374650000001238001",
    is_active: 1,
    company_id: 1,
    e_com_user: 1,
  },
  {
    id: 48,
    name: "Ella Jacob",
    zoho_id: "6374650000000789055",
    is_active: 1,
    company_id: 1,
    e_com_user: 1,
  },
] as const;

export const payments = [
  {
    id: 7104,
    amount: 1350,
    payment_date: "2026-10-01",
    customer: { company_name: "Highfallsextracts" },
    users: { name: "Jessica Joe" },
  },
  {
    id: 7105,
    amount: 4200,
    payment_date: "2026-10-15",
    customer: { company_name: "Seltzer8" },
    users: { name: "Harry Brook" },
  },
  {
    id: 7106,
    amount: 850,
    payment_date: "2026-10-18",
    customer: { company_name: "Balaika" },
    users: { name: "Ella Jacob" },
  },
  {
    id: 7107,
    amount: 2100,
    payment_date: "2026-10-20",
    customer: { company_name: "TechNova" },
    users: { name: "Harry Brook" },
  },
] as const;

export const quotes = [
  {
    id: 20843,
    ref: "20068-2026-10",
    status: "Quote Created",
    amount: 5310,
    sale_rep: { name: "Harry Brook" },
    customer: { company_name: "Balaika" },
  },
  {
    id: 20844,
    ref: "20069-2026-10",
    status: "Sent",
    amount: 12500,
    sale_rep: { name: "Jessica Joe" },
    customer: { company_name: "Seltzer8" },
  },
  {
    id: 20845,
    ref: "20070-2026-10",
    status: "Negotiating",
    amount: 800,
    sale_rep: { name: "Ella Jacob" },
    customer: { company_name: "Highfallsextracts" },
  },
  {
    id: 20846,
    ref: "20071-2026-10",
    status: "Quote Created",
    amount: 3450,
    sale_rep: { name: "Harry Brook" },
    customer: { company_name: "TechNova" },
  },
] as const;

export const orders = [
  {
    id: 33516,
    inv_ref: "33516-2026-10",
    status_label: "Approved",
    payment_status_label: "Partially Paid",
    total_amount: 2590,
    amount_paid: 1295,
  },
  {
    id: 33517,
    inv_ref: "33517-2026-10",
    status_label: "Shipped",
    payment_status_label: "Paid",
    total_amount: 4500,
    amount_paid: 4500,
  },
  {
    id: 33518,
    inv_ref: "33518-2026-10",
    status_label: "Processing",
    payment_status_label: "Unpaid",
    total_amount: 1200,
    amount_paid: 0,
  },
  {
    id: 33519,
    inv_ref: "33519-2026-10",
    status_label: "Approved",
    payment_status_label: "Partially Paid",
    total_amount: 8900,
    amount_paid: 4000,
  },
] as const;

export const leads = [
  {
    _id: { $oid: "6a27422e7d68ce2685a0e807" },
    aiIntelligence: { buyingIntentScore: 0, tags: ["Emails Only"] },
    status: "New",
    leadDesk: { stage: "open" },
  },
  {
    _id: { $oid: "6b38533f8e79df3796b1f918" },
    aiIntelligence: {
      buyingIntentScore: 85,
      tags: ["High Priority", "Website"],
    },
    status: "Contacted",
    leadDesk: { stage: "in_progress" },
  },
  {
    _id: { $oid: "6c49644g9f80eg4807c2g029" },
    aiIntelligence: { buyingIntentScore: 40, tags: ["Newsletter"] },
    status: "Follow Up",
    leadDesk: { stage: "open" },
  },
  {
    _id: { $oid: "6d50755h0g91fh5918d3h130" },
    aiIntelligence: {
      buyingIntentScore: 95,
      tags: ["Direct Call", "Ready to Buy"],
    },
    status: "Qualified",
    leadDesk: { stage: "closing" },
  },
] as const;

// Pre-calculated backend summaries
export const mockDashboardMetrics = {
  total_revenue: 8500,
  active_leads_count: 4,
  total_orders: 4,
  total_quotes: 4,
  total_payments: 4,
} as const;

export const mockDashboardCharts = [
  {
    id: "revenue",
    title: "Total revenue",
    metric: mockDashboardMetrics.total_revenue,
    currency: true,
    period: "Revenue trend · mock series",
    labels: ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"],
    values: [1200, 2500, 2200, 4500, 4100, 6800, 8500],
    accent: "#a3e635",
  },
  {
    id: "leads",
    title: "Active leads",
    metric: mockDashboardMetrics.active_leads_count,
    currency: false,
    period: "Lead trend · mock series",
    labels: ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"],
    values: [0, 1, 1, 2, 2, 3, 4],
    accent: "#6ea8fe",
  },
  {
    id: "orders",
    title: "Orders",
    metric: mockDashboardMetrics.total_orders,
    currency: false,
    period: "Order trend · mock series",
    labels: ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"],
    values: [0, 0, 1, 2, 2, 3, 4],
    accent: "#c084fc",
  },
] as const;

export const dashboardData = {
  displayDate: "Tuesday, October 20",
} as const;

export const chatData = {
  assistantName: "Biztek Assistant",
  status: "Ready",
  markdownTitle: "Order status updated",
  orderId: "ORD-77104",
  markdownBody:
    "is packed and ready for dispatch. The customer has been notified.",
  structuredJson: `{
  "order_id": "ORD-77104",
  "status": "ready_to_dispatch",
  "customer": "Juniper Retail Group",
  "items": 6
}`,
} as const;

export const voiceConsoleData = {
  status: "Voice channel ready",
  instruction: "Press and hold to speak",
  lastTranscript: "No recent voice activity",
} as const;
