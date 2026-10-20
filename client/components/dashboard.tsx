"use client";

import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import { Activity, Boxes, FileText, Users, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import {
  dashboardData,
  leads,
  mockDashboardCharts,
  mockDashboardMetrics,
  orders,
  quotes,
} from "@/lib/mockData";

type ChartData = (typeof mockDashboardCharts)[number];
type LiveChartData = Omit<ChartData, "labels" | "values"> & {
  labels: string[];
  values: number[];
};

const mockLiveChartEvents = [
  { label: "15:05", revenue: 8600, leads: 4, orders: 4 },
  { label: "15:10", revenue: 8950, leads: 5, orders: 5 },
  { label: "15:15", revenue: 9200, leads: 5, orders: 6 },
  { label: "15:20", revenue: 9450, leads: 6, orders: 6 },
] as const;

function ChartTile({ chart }: { chart: LiveChartData }) {
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
          <p className="mt-3 text-2xl font-semibold tracking-tight">
            {chart.currency ? "$" : ""}
            {chart.metric}
          </p>
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
  const liveEventIndexRef = useRef(0);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      const event = mockLiveChartEvents[liveEventIndexRef.current];
      liveEventIndexRef.current = (liveEventIndexRef.current + 1) % mockLiveChartEvents.length;

      const valuesByChart = {
        revenue: event.revenue,
        leads: event.leads,
        orders: event.orders,
      };

      setCharts((currentCharts) =>
        currentCharts.map((chart) => ({
          ...chart,
          labels: [...chart.labels.slice(1), event.label],
          values: [...chart.values.slice(1), valuesByChart[chart.id]],
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
        {charts.map((chart) => (
          <ChartTile key={chart.id} chart={chart} />
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <MetricCard label="Active leads" value={mockDashboardMetrics.active_leads_count} icon={Users} />
        <MetricCard label="Total orders" value={mockDashboardMetrics.total_orders} icon={Boxes} />
        <MetricCard label="Total quotes" value={mockDashboardMetrics.total_quotes} icon={FileText} />
      </div>

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
