"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { ArrowUpRight, Activity } from "lucide-react";
import { dashboardData } from "@/lib/mockData";

type ChartData = (typeof dashboardData.charts)[number];

function ChartTile({ chart }: { chart: ChartData }) {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = chartRef.current;
    if (!element) return;

    const instance = echarts.init(element);
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

    const resizeObserver = new ResizeObserver(() => instance.resize());
    resizeObserver.observe(element);

    return () => {
      resizeObserver.disconnect();
      instance.dispose();
    };
  }, [chart]);

  return (
    <article className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-muted">{chart.title}</p>
          <p className="mt-3 text-2xl font-semibold tracking-tight">{chart.value}</p>
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
        {dashboardData.metrics.map((metric) => (
          <article key={metric.label} className="rounded-xl border border-border bg-surface p-5">
            <p className="text-sm text-muted">{metric.label}</p>
            <div className="mt-3 flex items-end justify-between gap-2">
              <p className="text-2xl font-semibold tracking-tight">{metric.value}</p>
              <span className="inline-flex items-center gap-1 text-xs text-accent">
                {metric.change}
                <ArrowUpRight size={14} aria-hidden="true" />
              </span>
            </div>
          </article>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {dashboardData.charts.map((chart) => (
          <ChartTile key={chart.id} chart={chart} />
        ))}
      </div>
    </section>
  );
}
