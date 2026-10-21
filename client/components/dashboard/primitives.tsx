"use client";

// Small presentational pieces the dashboard panels are built from. Styling and
// markup are the ones the dashboard has always rendered.

import type { LucideIcon } from "lucide-react";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

export function KpiCard({
  icon: Icon,
  label,
  value,
  detail,
  onActivate,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
  onActivate?: () => void;
}) {
  return (
    <article className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-muted">{label}</p>
        <Icon size={16} className="text-muted" aria-hidden="true" />
      </div>
      {onActivate ? (
        <button
          type="button"
          onClick={onActivate}
          title={`Open the ${label} tab`}
          className="mt-3 block text-left text-2xl font-semibold tracking-tight transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          {value}
        </button>
      ) : (
        <p className="mt-3 text-2xl font-semibold tracking-tight">{value}</p>
      )}
      <p className="mt-1 text-xs text-muted">{detail}</p>
    </article>
  );
}

export function ChartCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 overflow-hidden rounded-xl border border-border bg-surface p-5">
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-1 text-xs text-muted">{subtitle}</p>
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

export function AttentionRow({
  label,
  count,
  amount,
  detail,
  onClick,
}: {
  label: string;
  count?: number;
  amount?: string;
  detail?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-between gap-3 rounded-lg border border-border bg-background px-4 py-3 text-left transition-colors hover:border-accent/40"
    >
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="mt-1 text-xs text-muted">
          {count === undefined ? "Loading..." : `${count} items`}
          {amount ? ` · ${amount}` : ""}
        </p>
        {detail && <p className="mt-1 text-[11px] text-muted">{detail}</p>}
      </div>
      <ChevronRight size={15} className="text-muted" aria-hidden="true" />
    </button>
  );
}

export interface FilterOption {
  label: string;
  value: number;
  options: number[];
  suffix: string;
  onChange: (value: number) => void;
}

export function FilterBar({
  title,
  description,
  filters,
}: {
  title: string;
  description: string;
  filters: FilterOption[];
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-4 rounded-xl border border-border bg-surface px-5 py-4">
      <div className="max-w-2xl">
        <h2 className="font-semibold">{title}</h2>
        <p className="mt-1 text-xs text-muted">{description}</p>
      </div>
      <div className="flex flex-wrap gap-3">
        {filters.map((filter) => (
          <label key={filter.label} className="flex items-center gap-2 text-xs text-muted">
            {filter.label}
            <select
              value={filter.value}
              onChange={(event) => filter.onChange(Number(event.target.value))}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground outline-none focus-visible:border-ring"
            >
              {filter.options.map((option) => (
                <option key={option} value={option}>
                  {option} {filter.suffix}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </div>
  );
}

export function LoadingNote() {
  return (
    <p
      className="rounded-xl border border-border bg-surface px-4 py-6 text-center text-sm text-muted"
      role="status"
    >
      Loading signals from the backend...
    </p>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-red-400/30 bg-red-400/5 px-4 py-5 text-center">
      <p className="text-sm text-red-300">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-md border border-border px-3 py-1.5 text-xs text-muted hover:bg-surface-raised hover:text-foreground"
      >
        Try again
      </button>
    </div>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="px-4 py-8 text-center text-xs text-muted">{children}</p>;
}
