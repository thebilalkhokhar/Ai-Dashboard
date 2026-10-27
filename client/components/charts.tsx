"use client";

import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import type { QuotesPerDay, RepMetric, RevenuePerDay } from "@/types/api";

const AXIS_COLOR = "#838e9f";
const GRID_COLOR = "#1e2636";
const TOOLTIP_BG = "#1a2230";
const TOOLTIP_BORDER = "#1e2636";

function ChartCanvas({
  option,
  ariaLabel,
  dataTestId,
  className = "h-44 w-full",
}: {
  option: echarts.EChartsOption;
  ariaLabel: string;
  dataTestId?: string;
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
    chartRef.current?.setOption(
      {
        animation: true,
        animationDuration: 650,
        animationEasing: "cubicOut",
        ...option,
      },
      true,
    );
  }, [option]);

  return (
    <div
      ref={containerRef}
      className={`min-w-0 overflow-hidden ${className}`}
      role="img"
      aria-label={ariaLabel}
      data-testid={dataTestId}
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
      grid: { left: 44, right: 12, top: 16, bottom: 22, containLabel: false },
      tooltip: {
        trigger: "axis",
        backgroundColor: TOOLTIP_BG,
        borderColor: TOOLTIP_BORDER,
        borderWidth: 1,
        padding: [8, 12],
        textStyle: { color: "#f0f3f8", fontSize: 12, fontFamily: "var(--font-sans)" },
        formatter: (params: unknown) => {
          const first = (Array.isArray(params) ? params[0] : params) as
            | { dataIndex?: number }
            | undefined;
          const index = first?.dataIndex ?? 0;
          const row = data[index];
          if (!row) return "";
          return `<div style="font-size:11px;color:#838e9f;margin-bottom:2px">${row.date}</div><div style="font-size:13px;font-weight:700;color:#a3e635">${row.total_display}</div>`;
        },
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: data.map((row) => shortDate(row.date)),
        axisLabel: { color: AXIS_COLOR, fontSize: 10, interval: 2, fontFamily: "var(--font-mono)" },
        axisLine: { lineStyle: { color: GRID_COLOR } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        show: true,
        axisLabel: {
          color: AXIS_COLOR,
          fontSize: 10,
          fontFamily: "var(--font-mono)",
          formatter: (val: number) => {
            if (val >= 1000) return `$${(val / 1000).toFixed(0)}k`;
            return `$${val}`;
          },
        },
        splitLine: { lineStyle: { color: GRID_COLOR, type: "dashed" } },
      },
      series: [
        {
          type: "line",
          name: "Revenue",
          data: data.map((row) => row.total),
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#a3e635", width: 2.2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(163, 230, 53, 0.22)" },
              { offset: 1, color: "rgba(163, 230, 53, 0.01)" },
            ]),
          },
        },
      ],
    }),
    [data],
  );

  return (
    <ChartCanvas
      option={option}
      ariaLabel="Revenue received per day for the last 14 days"
      dataTestId="revenue-chart"
    />
  );
}

export function QuoteVolumeChart({ data }: { data: QuotesPerDay[] }) {
  const option = useMemo<echarts.EChartsOption>(
    () => ({
      grid: { left: 34, right: 12, top: 16, bottom: 22, containLabel: false },
      tooltip: {
        trigger: "axis",
        backgroundColor: TOOLTIP_BG,
        borderColor: TOOLTIP_BORDER,
        borderWidth: 1,
        padding: [8, 12],
        textStyle: { color: "#f0f3f8", fontSize: 12, fontFamily: "var(--font-sans)" },
        formatter: (params: unknown) => {
          const first = (Array.isArray(params) ? params[0] : params) as
            | { dataIndex?: number; value?: unknown }
            | undefined;
          const row = data[first?.dataIndex ?? 0];
          if (!row) return "";
          return `<div style="font-size:11px;color:#838e9f;margin-bottom:2px">${row.date}</div><div style="font-size:13px;font-weight:700;color:#38bdf8">${row.count} quotes</div>`;
        },
      },
      xAxis: {
        type: "category",
        data: data.map((row) => shortDate(row.date)),
        axisLabel: { color: AXIS_COLOR, fontSize: 10, interval: 2, fontFamily: "var(--font-mono)" },
        axisLine: { lineStyle: { color: GRID_COLOR } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        show: true,
        minInterval: 1,
        axisLabel: { color: AXIS_COLOR, fontSize: 10, fontFamily: "var(--font-mono)" },
        splitLine: { lineStyle: { color: GRID_COLOR, type: "dashed" } },
      },
      series: [
        {
          type: "bar",
          name: "Quotes",
          data: data.map((row) => row.count),
          barMaxWidth: 12,
          itemStyle: {
            color: "#38bdf8",
            borderRadius: [4, 4, 0, 0],
          },
        },
      ],
    }),
    [data],
  );

  return (
    <ChartCanvas
      option={option}
      ariaLabel="Quotes created per day for the last 14 days"
      dataTestId="quote-chart"
    />
  );
}

export function TopRepsChart({ reps }: { reps: RepMetric[] }) {
  const option = useMemo<echarts.EChartsOption>(
    () => ({
      grid: { left: 96, right: 140, top: 6, bottom: 6, containLabel: false },
      tooltip: {
        trigger: "item",
        backgroundColor: TOOLTIP_BG,
        borderColor: TOOLTIP_BORDER,
        borderWidth: 1,
        padding: [8, 12],
        textStyle: { color: "#f0f3f8", fontSize: 12, fontFamily: "var(--font-sans)" },
        formatter: (params: unknown) => {
          const first = Array.isArray(params) ? params[0] : params;
          const rep = reps[(first as { dataIndex?: number })?.dataIndex ?? 0];
          if (!rep) return "";
          return `<div style="font-weight:600;color:#f0f3f8">${rep.name}</div><div style="color:#a3e635;font-weight:700;font-size:13px">${rep.total_display}</div><div style="color:#838e9f;font-size:11px">${rep.count} ${rep.count === 1 ? "payment" : "payments"}</div>`;
        },
      },
      xAxis: { type: "value", show: false },
      yAxis: {
        type: "category",
        inverse: true,
        data: reps.map((rep) => rep.name),
        axisLabel: { color: "#f0f3f8", fontSize: 11, width: 88, overflow: "truncate", fontWeight: 500 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: "bar",
          data: reps.map((rep) => rep.total),
          barMaxWidth: 14,
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
              { offset: 0, color: "rgba(163, 230, 53, 0.4)" },
              { offset: 1, color: "#a3e635" },
            ]),
            borderRadius: [0, 4, 4, 0],
          },
          label: {
            show: true,
            position: "right",
            color: "#838e9f",
            fontSize: 10,
            fontFamily: "var(--font-mono)",
            formatter: (params: unknown) => {
              const first = params as { dataIndex?: number };
              const rep = reps[first.dataIndex ?? 0];
              if (!rep) return "";
              return `${rep.total_display} · ${rep.count} ${rep.count === 1 ? "payment" : "payments"}`;
            },
          },
        },
      ],
    }),
    [reps],
  );

  return (
    <ChartCanvas
      option={option}
      ariaLabel="Top sales reps by payments this month"
      dataTestId="reps-chart"
      className="h-52 w-full"
    />
  );
}
