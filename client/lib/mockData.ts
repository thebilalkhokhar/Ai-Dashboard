export const dashboardData = {
  displayDate: "Tuesday, October 20",
  metrics: [
    { label: "Active conversations", value: "1,284", change: "+12.8%" },
    { label: "Payments processed", value: "$284.6K", change: "+8.2%" },
    { label: "Approval rate", value: "98.2%", change: "+1.4%" },
  ],
  charts: [
    {
      id: "conversations",
      title: "Conversation volume",
      period: "Last 24 hours",
      value: "1,284",
      labels: ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"],
      values: [42, 58, 51, 76, 68, 92, 84],
      accent: "#a3e635",
    },
    {
      id: "payments",
      title: "Payment volume",
      period: "Last 24 hours",
      value: "$42,680",
      labels: ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"],
      values: [24, 38, 33, 59, 48, 72, 65],
      accent: "#6ea8fe",
    },
  ],
} as const;

export const approvalInboxData = {
  pendingCount: "03",
} as const;

export const approvalRequests = [
  {
    id: "APR-2048",
    title: "Enterprise renewal quote",
    description: "Northstar Logistics · Annual plan · 120 seats",
    amount: "$48,000.00",
    requestedBy: "Sales Assistant",
    submittedAt: "Today, 10:42 AM",
    category: "Quote",
  },
  {
    id: "APR-2047",
    title: "Vendor payment",
    description: "Cloudline Infrastructure · Invoice CL-9082",
    amount: "$12,450.00",
    requestedBy: "Finance Agent",
    submittedAt: "Today, 10:18 AM",
    category: "Payment",
  },
  {
    id: "APR-2046",
    title: "Priority fulfillment order",
    description: "Juniper Retail Group · Order ORD-77104",
    amount: "$8,920.00",
    requestedBy: "Operations Agent",
    submittedAt: "Today, 9:56 AM",
    category: "Order",
  },
] as const;

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
