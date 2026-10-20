"use client";

import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import type { QuotesPerDay, RepMetric, RevenuePerDay } from "@/types/api";

const AXIS_COLOR = "#737d8f";
const GRID_COLOR = "#252b38";

function ChartCanvas({
  option,
  ariaLabel,
  className = "h-44 w-full",
}: {
  option: echarts.EChartsOption;
  ariaLabel: string;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    const instance = echarts.init(element);
    chartRef.current = instance;
    const resizeObserver = new ResizeObserver(() => instance.resize());
    resizeObserver.observe(element);

    return () => {
      resizeObserver.disconnect();
      instance.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption({ ...option, animation: false }, true);
  }, [option]);

  return (
    <div
      ref={containerRef}
      className={`min-w-0 overflow-hidden ${className}`}
      role="img"
      aria-label={ariaLabel}
    />
  );
}

function shortDate(date: string): string {
  // "2026-10-07" -> "10-07" (label shortening only)
  return date.length >= 10 ? date.slice(5) : date;
}

export function RevenueTrendChart({ data }: { data: RevenuePerDay[] }) {
  const option = useMemo<echarts.EChartsOption>(
    () => ({
      grid: { left: 0, right: 0, top: 14, bottom: 4, containLabel: false },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#151a24",
        borderColor: "#252b38",
        textStyle: { color: "#eef1f6", fontSize: 12 },
        formatter: (params: unknown) => {
          const first = (Array.isArray(params) ? params[0] : params) as
            | { dataIndex?: number }
            | undefined;
          const index = first?.dataIndex ?? 0;
          const row = data[index];
          return row ? `${row.date}<br/><b>${row.total_display}</b>` : "";
        },
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: data.map((row) => shortDate(row.date)),
        axisLabel: { color: AXIS_COLOR, fontSize: 10, interval: 2 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        show: false,
        splitLine: { lineStyle: { color: GRID_COLOR } },
      },
      series: [
        {
          type: "line",
          name: "Revenue",
          data: data.map((row) => row.total),
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#a3e635", width: 2 },
          areaStyle: { color: "rgba(163, 230, 53, 0.14)" },
        },
      ],
    }),
    [data],
  );

  return (
    <ChartCanvas
      option={option}
      ariaLabel="Revenue received per day for the last 14 days"
    />
  );
}

export function QuoteVolumeChart({ data }: { data: QuotesPerDay[] }) {
  const option = useMemo<echarts.EChartsOption>(
    () => ({
      grid: { left: 0, right: 0, top: 14, bottom: 4, containLabel: false },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#151a24",
        borderColor: "#252b38",
        textStyle: { color: "#eef1f6", fontSize: 12 },
        formatter: (params: unknown) => {
          const first = (Array.isArray(params) ? params[0] : params) as
            | { dataIndex?: number; value?: unknown }
            | undefined;
          const row = data[first?.dataIndex ?? 0];
          return row ? `${row.date}<br/><b>${row.count} quotes</b>` : "";
        },
      },
      xAxis: {
        type: "category",
        data: data.map((row) => shortDate(row.date)),
        axisLabel: { color: AXIS_COLOR, fontSize: 10, interval: 2 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        show: false,
        splitLine: { lineStyle: { color: GRID_COLOR } },
      },
      series: [
        {
          type: "bar",
          name: "Quotes",
          data: data.map((row) => row.count),
          barMaxWidth: 10,
          itemStyle: { color: "#6ea8fe", borderRadius: [3, 3, 0, 0] },
        },
      ],
    }),
    [data],
  );

  return (
    <ChartCanvas option={option} ariaLabel="Quotes created per day for the last 14 days" />
  );
}

export function TopRepsChart({ reps }: { reps: RepMetric[] }) {
  const option = useMemo<echarts.EChartsOption>(
    () => ({
      grid: { left: 96, right: 78, top: 4, bottom: 4, containLabel: false },
      tooltip: {
        trigger: "item",
        backgroundColor: "#151a24",
        borderColor: "#252b38",
        textStyle: { color: "#eef1f6", fontSize: 12 },
        formatter: (params: unknown) => {
          const first = Array.isArray(params) ? params[0] : params;
          return (first as { name?: string } | undefined)?.name ?? "";
        },
      },
      xAxis: { type: "value", show: false },
      yAxis: {
        type: "category",
        inverse: true,
        data: reps.map((rep) => rep.name),
        axisLabel: { color: "#c7cede", fontSize: 11, width: 88, overflow: "truncate" },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: "bar",
          data: reps.map((rep) => rep.total),
          barMaxWidth: 12,
          itemStyle: { color: "#a3e635", borderRadius: [0, 4, 4, 0] },
          label: {
            show: true,
            position: "right",
            color: "#8c95a6",
            fontSize: 10,
            formatter: (params: unknown) => {
              const first = params as { dataIndex?: number };
              return reps[first.dataIndex ?? 0]?.total_display ?? "";
            },
          },
        },
      ],
    }),
    [reps],
  );

  return <ChartCanvas option={option} ariaLabel="Top sales reps by payments this month" className="h-48 w-full" />;
}
