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
    <article className="group rounded-xl border border-border bg-surface p-5 shadow-sm transition-all duration-200 hover:border-border-hover hover:bg-surface-raised/40 hover:shadow-card">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted">{label}</p>
        <div className="grid h-7 w-7 place-items-center rounded-lg border border-border/60 bg-surface-raised text-muted transition-colors group-hover:border-accent/40 group-hover:text-foreground">
          <Icon size={14} aria-hidden="true" />
        </div>
      </div>
      {onActivate ? (
        <button
          type="button"
          onClick={onActivate}
          title={`Open the ${label} tab`}
          className="mt-3 block text-left text-2xl font-bold font-mono tracking-tight text-foreground tabular-nums transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          {value}
        </button>
      ) : (
        <p className="mt-3 text-2xl font-bold font-mono tracking-tight text-foreground tabular-nums">{value}</p>
      )}
      <p className="mt-1 text-xs text-muted leading-relaxed">{detail}</p>
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
    <section className="min-w-0 overflow-hidden rounded-xl border border-border bg-surface p-5 shadow-sm transition-all duration-200 hover:border-border-hover hover:shadow-card">
      <div>
        <h2 className="text-sm font-semibold text-foreground tracking-tight">{title}</h2>
        <p className="mt-0.5 text-xs text-muted">{subtitle}</p>
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
      className="group flex w-full items-center justify-between gap-3.5 rounded-xl border border-border bg-surface-raised p-4 text-left transition-all duration-150 hover:border-accent/40 hover:bg-surface-overlay hover:shadow-sm"
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-foreground transition-colors group-hover:text-accent">{label}</p>
        <p className="mt-1 text-xs text-muted">
          {count === undefined ? "Loading..." : `${count} items`}
          {amount ? ` · ${amount}` : ""}
        </p>
        {detail && <p className="mt-1 text-[11px] text-muted/90">{detail}</p>}
      </div>
      <div className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-border bg-surface text-muted transition-colors group-hover:border-accent/40 group-hover:text-accent">
        <ChevronRight size={14} aria-hidden="true" />
      </div>
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
    <div className="mb-5 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-surface p-5 shadow-sm">
      <div className="max-w-2xl">
        <h2 className="text-base font-semibold text-foreground tracking-tight">{title}</h2>
        <p className="mt-1 text-xs text-muted leading-relaxed">{description}</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {filters.map((filter) => (
          <label key={filter.label} className="flex items-center gap-2 text-xs font-medium text-muted">
            <span>{filter.label}</span>
            <select
              value={filter.value}
              onChange={(event) => filter.onChange(Number(event.target.value))}
              className="rounded-lg border border-border bg-surface-raised px-3 py-1.5 text-xs font-semibold text-foreground outline-none transition-all hover:border-border-hover focus:border-accent focus:ring-1 focus:ring-accent/30 cursor-pointer"
            >
              {filter.options.map((option) => (
                <option key={option} value={option} className="bg-surface text-foreground">
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
      className="rounded-xl border border-border bg-surface px-4 py-8 text-center text-sm font-medium text-muted shadow-sm"
      role="status"
    >
      Loading signals from the backend...
    </p>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-negative-border bg-negative-muted px-4 py-6 text-center shadow-sm">
      <p className="text-sm font-medium text-negative-foreground">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3.5 inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3.5 py-1.5 text-xs font-semibold text-foreground shadow-sm transition-all hover:border-accent/40 hover:bg-surface-raised"
      >
        Try again
      </button>
    </div>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="px-4 py-8 text-center text-xs text-muted">{children}</p>;
}
