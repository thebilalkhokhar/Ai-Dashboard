"use client";

import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import {
  Activity,
  AlertTriangle,
  Boxes,
  FileText,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import {
  dashboardData,
  leads,
  mockDashboardCharts,
  mockSignalsReport,
  orders,
  quotes,
} from "@/lib/mockData";
import { fetchSignals } from "@/services/api";
import type {
  SignalsReport,
  SignalsSummary,
  StaleQuote,
  StalledOrder,
  UnpaidInvoice,
} from "@/types/api";

type ChartData = (typeof mockDashboardCharts)[number];
type LiveChartData = Omit<ChartData, "labels" | "values"> & {
  labels: string[];
  values: number[];
};

const mockLiveChartEvents = [
  { label: "15:05", values: { stale_quotes: 4, unpaid_invoices: 4, stalled_orders: 2 } },
  { label: "15:10", values: { stale_quotes: 5, unpaid_invoices: 4, stalled_orders: 3 } },
  { label: "15:15", values: { stale_quotes: 5, unpaid_invoices: 5, stalled_orders: 3 } },
  { label: "15:20", values: { stale_quotes: 6, unpaid_invoices: 5, stalled_orders: 4 } },
] as const;

function ChartTile({
  chart,
  summary,
}: {
  chart: LiveChartData;
  summary: { count: number; total_amount: number };
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const element = chartRef.current;
    if (!element) return;

    const instance = echarts.init(element);
    chartInstanceRef.current = instance;
    const resizeObserver = new ResizeObserver(() => instance.resize());
    resizeObserver.observe(element);

    return () => {
      resizeObserver.disconnect();
      instance.dispose();
      chartInstanceRef.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = chartInstanceRef.current;
    if (!instance) return;

    instance.setOption({
      animation: false,
      grid: { left: 0, right: 0, top: 10, bottom: 4, containLabel: false },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: chart.labels,
        axisLabel: { color: "#737d8f", fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        show: false,
        splitLine: { lineStyle: { color: "#252b38" } },
      },
      series: [
        {
          type: "line",
          data: chart.values,
          smooth: true,
          symbol: "none",
          lineStyle: { color: chart.accent, width: 2 },
          areaStyle: { color: `${chart.accent}20` },
        },
      ],
    });
  }, [chart.accent, chart.labels, chart.values]);

  return (
    <article className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-muted">{chart.title}</p>
          <p className="mt-3 text-2xl font-semibold tracking-tight">{summary.count}</p>
          <p className="mt-1 text-xs text-muted">Total amount: {summary.total_amount}</p>
        </div>
        <span className="rounded-lg border border-border p-2 text-muted" aria-hidden="true">
          <Activity size={17} />
        </span>
      </div>
      <div ref={chartRef} className="mt-5 h-36 w-full" role="img" aria-label={`${chart.title}, ${chart.period}`} />
      <p className="mt-2 text-xs text-muted">{chart.period}</p>
    </article>
  );
}

export function Dashboard() {
  const [charts, setCharts] = useState<LiveChartData[]>(() =>
    mockDashboardCharts.map((chart) => ({
      ...chart,
      labels: [...chart.labels],
      values: [...chart.values],
    })),
  );
  const [signalsReport, setSignalsReport] = useState<SignalsReport>(mockSignalsReport);
  const liveEventIndexRef = useRef(0);

  useEffect(() => {
    let isActive = true;

    fetchSignals().then((report) => {
      if (isActive) {
        setSignalsReport(report);
      }
    });

    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      const event = mockLiveChartEvents[liveEventIndexRef.current];
      liveEventIndexRef.current = (liveEventIndexRef.current + 1) % mockLiveChartEvents.length;

      setCharts((currentCharts) =>
        currentCharts.map((chart) => ({
          ...chart,
          labels: [...chart.labels.slice(1), event.label],
          values: [...chart.values.slice(1), event.values[chart.id]],
        })),
      );
    }, 4000);

    return () => window.clearInterval(intervalId);
  }, []);

  return (
    <section aria-labelledby="dashboard-title" className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-muted">{dashboardData.displayDate}</p>
          <h1 id="dashboard-title" className="mt-1 text-2xl font-semibold tracking-tight">
            Command center
          </h1>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-muted">
          <span className="h-2 w-2 rounded-full bg-accent" />
          Systems operational
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {charts.map((chart) => {
          const summary = signalsReport.summary[chart.id];
          return <ChartTile key={chart.id} chart={chart} summary={summary} />;
        })}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <MetricCard
          label="Unpaid balance"
          value={signalsReport.summary.unpaid_invoices.total_amount}
          icon={AlertTriangle}
        />
        <MetricCard
          label="Unpaid invoices"
          value={signalsReport.summary.unpaid_invoices.count}
          icon={FileText}
        />
        <MetricCard
          label="Partially paid"
          value={signalsReport.summary.unpaid_invoices.partially_paid_count}
          icon={Boxes}
        />
      </div>

      <AttentionNeeded report={signalsReport} />

      <div className="grid gap-5 xl:grid-cols-2">
        <DataTable title="Orders" icon={Boxes}>
          <table className="w-full min-w-[620px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Order</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Payment</th>
                <th className="px-4 py-3 text-right font-medium">Total</th>
                <th className="px-4 py-3 text-right font-medium">Paid</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {orders.map((order) => (
                <tr key={order.id}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{order.inv_ref}</p>
                    <p className="mt-1 text-xs text-muted">ID {order.id}</p>
                  </td>
                  <td className="px-4 py-3 text-muted">{order.status_label}</td>
                  <td className="px-4 py-3 text-muted">{order.payment_status_label}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{order.total_amount}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{order.amount_paid}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataTable>

        <DataTable title="Quotes" icon={FileText}>
          <table className="w-full min-w-[580px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Quote</th>
                <th className="px-4 py-3 font-medium">Customer</th>
                <th className="px-4 py-3 font-medium">Sales rep</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {quotes.map((quote) => (
                <tr key={quote.id}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{quote.ref}</p>
                    <p className="mt-1 text-xs text-muted">ID {quote.id}</p>
                  </td>
                  <td className="px-4 py-3">{quote.customer.company_name}</td>
                  <td className="px-4 py-3 text-muted">{quote.sale_rep.name}</td>
                  <td className="px-4 py-3 text-muted">{quote.status}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{quote.amount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataTable>

        <DataTable title="Leads" icon={Users}>
          <table className="w-full min-w-[600px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Mongo ID</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Stage</th>
                <th className="px-4 py-3 text-right font-medium">Intent score</th>
                <th className="px-4 py-3 font-medium">Tags</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {leads.map((lead) => (
                <tr key={lead._id.$oid}>
                  <td className="px-4 py-3 font-mono text-xs text-muted">{lead._id.$oid}</td>
                  <td className="px-4 py-3">{lead.status}</td>
                  <td className="px-4 py-3 text-muted">{lead.leadDesk.stage}</td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {lead.aiIntelligence.buyingIntentScore}
                  </td>
                  <td className="px-4 py-3">
                    {lead.aiIntelligence.tags.map((tag) => (
                      <span key={tag} className="rounded-md bg-surface-raised px-2 py-1 text-xs text-muted">
                        {tag}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataTable>
      </div>
    </section>
  );
}

function MetricCard({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: LucideIcon;
}) {
  return (
    <article className="flex items-center justify-between rounded-xl border border-border bg-surface p-5">
      <div>
        <p className="text-sm text-muted">{label}</p>
        <p className="mt-3 text-2xl font-semibold tracking-tight">{value}</p>
      </div>
      <span className="rounded-lg border border-border p-2 text-muted" aria-hidden="true">
        <Icon size={17} />
      </span>
    </article>
  );
}

function DataTable({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-surface" aria-label={`${title} data`}>
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Icon size={16} className="text-muted" aria-hidden="true" />
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}

function AttentionNeeded({ report }: { report: SignalsReport }) {
  const alertGroups: {
    id: string;
    title: string;
    summary: SignalsSummary["stale_quotes"] | SignalsSummary["unpaid_invoices"] | SignalsSummary["stalled_orders"];
    records: (StaleQuote | UnpaidInvoice | StalledOrder)[];
    icon: LucideIcon;
    details?: string;
  }[] = [
    {
      id: "stale-quotes",
      title: "Stale quotes",
      summary: report.summary.stale_quotes,
      records: report.stale_quotes,
      icon: FileText,
    },
    {
      id: "unpaid-invoices",
      title: "Unpaid invoices",
      summary: report.summary.unpaid_invoices,
      records: report.unpaid_invoices,
      icon: AlertTriangle,
      details: `Overdue: ${report.summary.unpaid_invoices.overdue_count} · Unpaid: ${report.summary.unpaid_invoices.unpaid_count} · Partially paid: ${report.summary.unpaid_invoices.partially_paid_count}`,
    },
    {
      id: "stalled-orders",
      title: "Stalled orders",
      summary: report.summary.stalled_orders,
      records: report.stalled_orders,
      icon: Boxes,
    },
  ];

  return (
    <section aria-labelledby="attention-title" className="rounded-xl border border-border bg-surface">
      <div className="flex items-center gap-3 border-b border-border px-5 py-4">
        <AlertTriangle size={17} className="text-accent" aria-hidden="true" />
        <div>
          <h2 id="attention-title" className="font-semibold">Attention needed</h2>
          <p className="mt-1 text-xs text-muted">
            Generated {report.generated_at} · Data range: {report.summary.data_from} to {report.summary.data_to}
          </p>
        </div>
      </div>
      <div className="grid gap-4 p-4 lg:grid-cols-3">
        {alertGroups.map((group) => {
          const Icon = group.icon;
          return (
            <article key={group.id} className="rounded-lg border border-border bg-[#0c0f15] p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-medium">{group.title}</h3>
                  <p className="mt-2 text-xs text-muted">
                    {group.summary.count} items · Total amount: {group.summary.total_amount}
                  </p>
                  {group.details && <p className="mt-1 text-xs text-muted">{group.details}</p>}
                </div>
                <Icon size={16} className="text-muted" aria-hidden="true" />
              </div>
              <div className="mt-4 space-y-2">
                {group.records.length === 0 ? (
                  <p className="text-xs text-muted">No records in this signal.</p>
                ) : (
                  group.records.map((record, index) => (
                    <pre
                      key={`${group.id}-${index}`}
                      className="overflow-x-auto rounded-md border border-border bg-surface p-3 text-[11px] leading-5 text-muted"
                    >
                      {JSON.stringify(record, null, 2)}
                    </pre>
                  ))
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
