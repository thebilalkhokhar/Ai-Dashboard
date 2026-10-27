"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { AlertCircle, Building2, Calendar, FileText, Info, Mail, Phone, Search, ShoppingBag } from "lucide-react";
import { fetchCustomerContext } from "@/services/api";
import type { CustomerContext } from "@/types/api";

export function CustomerContextPanel({
  selectedCustomerId,
}: {
  selectedCustomerId: number | null;
}) {
  const [customerId, setCustomerId] = useState(
    selectedCustomerId === null ? "" : String(selectedCustomerId),
  );
  const [customer, setCustomer] = useState<CustomerContext | null>(null);
  const [isLoading, setIsLoading] = useState(selectedCustomerId !== null);
  const [errorMessage, setErrorMessage] = useState("");
  const requestIdRef = useRef(0);

  const loadCustomerContext = useCallback(async (parsedId: number) => {
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    setErrorMessage("");
    setCustomer(null);

    try {
      setCustomer(await fetchCustomerContext(parsedId));
    } catch (error) {
      console.error("Unable to load customer context.", error);
      setErrorMessage(
        error instanceof Error ? error.message : "Customer context could not be loaded.",
      );
    } finally {
      if (requestId === requestIdRef.current) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (selectedCustomerId === null) return;

    let isActive = true;
    const requestId = ++requestIdRef.current;

    fetchCustomerContext(selectedCustomerId)
      .then((context) => {
        if (isActive && requestId === requestIdRef.current) {
          setCustomer(context);
        }
      })
      .catch((error: unknown) => {
        if (!isActive || requestId !== requestIdRef.current) return;
        setErrorMessage(
          error instanceof Error ? error.message : "Customer context could not be loaded.",
        );
      })
      .finally(() => {
        if (isActive && requestId === requestIdRef.current) {
          setIsLoading(false);
        }
      });

    return () => {
      isActive = false;
    };
  }, [selectedCustomerId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsedId = Number(customerId);
    if (isLoading || !Number.isInteger(parsedId) || parsedId < 1) {
      setErrorMessage("Enter a valid positive customer ID.");
      return;
    }

    await loadCustomerContext(parsedId);
  }

  return (
    <section
      aria-labelledby="customer-context-title"
      className="overflow-hidden rounded-2xl border border-border/80 bg-surface/80 backdrop-blur-md shadow-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border/70 px-5 py-4">
        <div>
          <h2 id="customer-context-title" className="text-base font-semibold tracking-tight text-foreground">
            Customer context
          </h2>
          <p className="mt-0.5 text-xs text-muted">
            Look up by the backend customer ID. There is no customer search endpoint.
          </p>
        </div>
        <form onSubmit={handleSubmit} className="flex w-full items-center gap-2 sm:w-auto">
          <label className="sr-only" htmlFor="customer-id">Customer ID</label>
          <div className="relative flex-1 sm:w-44">
            <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-muted">
              #
            </span>
            <input
              id="customer-id"
              type="number"
              min="1"
              step="1"
              required
              inputMode="numeric"
              value={customerId}
              onChange={(event) => setCustomerId(event.target.value)}
              placeholder="Customer ID"
              disabled={isLoading}
              className="w-full rounded-xl border border-border bg-surface-raised/70 pl-7 pr-3 py-2 font-mono text-sm text-foreground outline-none transition-all placeholder:text-muted focus-visible:border-accent focus-visible:ring-1 focus-visible:ring-accent"
            />
          </div>
          <button
            type="submit"
            disabled={isLoading}
            className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-[#11150b] shadow-sm transition-all hover:opacity-90 active:scale-[0.98] disabled:cursor-wait disabled:opacity-60"
          >
            <Search size={14} aria-hidden="true" />
            {isLoading ? "Loading..." : "Load"}
          </button>
        </form>
      </div>

      <div className="p-5">
        {errorMessage && (
          <div className="mb-4 flex items-center gap-2 rounded-xl border border-negative-border bg-negative-muted px-4 py-3 text-sm text-negative" role="alert">
            <AlertCircle size={16} aria-hidden="true" className="shrink-0" />
            <p>{errorMessage}</p>
          </div>
        )}
        {isLoading ? (
          <div className="space-y-4 py-6" role="status">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 animate-pulse rounded-xl bg-surface-raised" />
              <div className="space-y-2">
                <div className="h-4 w-40 animate-pulse rounded bg-surface-raised" />
                <div className="h-3 w-24 animate-pulse rounded bg-surface-raised" />
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              <div className="h-28 animate-pulse rounded-xl bg-surface-raised" />
              <div className="h-28 animate-pulse rounded-xl bg-surface-raised" />
              <div className="h-28 animate-pulse rounded-xl bg-surface-raised" />
            </div>
            <p className="text-center text-xs text-muted">Loading customer context...</p>
          </div>
        ) : customer ? (
          <CustomerDetails customer={customer} />
        ) : (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/80 p-8 text-center">
            <Building2 size={24} className="text-muted/60 mb-2" aria-hidden="true" />
            <p className="text-sm font-medium text-foreground">No customer loaded</p>
            <p className="mt-1 text-xs text-muted">Enter a customer ID to view its account and lead context.</p>
          </div>
        )}
      </div>
    </section>
  );
}

function CustomerDetails({ customer }: { customer: CustomerContext }) {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4 rounded-xl border border-border/70 bg-surface-raised/40 p-4">
        <div className="flex items-center gap-3.5">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent/15 text-accent border border-accent/20">
            <Building2 size={20} aria-hidden="true" />
          </span>
          <div>
            <h3 className="text-base font-semibold tracking-tight text-foreground">
              {customer.company_name || `Customer ${customer.customer_id}`}
            </h3>
            <p className="mt-0.5 font-mono text-xs text-muted">Customer ID {customer.customer_id}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
          <span className="inline-flex items-center gap-1.5 rounded-md bg-surface px-2.5 py-1 border border-border/60">
            <Mail size={13} aria-hidden="true" className="text-muted" />
            <span className="text-foreground/90">{customer.contact.email || "No email"}</span>
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-md bg-surface px-2.5 py-1 border border-border/60">
            <Phone size={13} aria-hidden="true" className="text-muted" />
            <span className="text-foreground/90">{customer.contact.phone || "No phone"}</span>
          </span>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <SummaryCard
          title="Quotes"
          icon={FileText}
          count={customer.quotes.count}
          total={customer.quotes.total_amount}
        >
          <CountList counts={customer.quotes.status_counts} />
          <DateRange first={customer.quotes.first_at} last={customer.quotes.last_at} />
        </SummaryCard>
        <SummaryCard
          title="Orders"
          icon={ShoppingBag}
          count={customer.orders.count}
          total={customer.orders.total_amount}
        >
          <p className="font-mono text-xs text-foreground/90">
            Paid {formatAmount(customer.orders.amount_paid)} · Balance {formatAmount(customer.orders.balance)}
          </p>
          <p className="text-xs text-muted">
            Unpaid {customer.orders.unpaid_count} · Partial {customer.orders.partially_paid_count} · Paid {customer.orders.fully_paid_count}
          </p>
          <CountList counts={customer.orders.payment_status_counts} />
          <DateRange first={customer.orders.first_at} last={customer.orders.last_at} />
        </SummaryCard>
        <SummaryCard
          title="Payments"
          icon={Calendar}
          count={customer.payments.count}
          total={customer.payments.total_amount}
        >
          <DateRange first={customer.payments.first_at} last={customer.payments.last_at} />
        </SummaryCard>
      </div>

      <section aria-labelledby="customer-leads-title" className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 id="customer-leads-title" className="text-sm font-semibold tracking-tight text-foreground">
            Lead matches
          </h4>
          <span className="rounded-full bg-surface-raised px-2.5 py-0.5 font-mono text-xs text-muted border border-border/60">
            {customer.leads.length} records
          </span>
        </div>
        {customer.leads.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/80 bg-surface-raised/20 p-5 text-center text-xs text-muted">
            No matching lead outcomes were returned.
          </div>
        ) : (
          <div className="space-y-3">
            {customer.leads.map((lead, index) => (
              <article
                key={`${lead.lead_ref ?? "lead"}-${index}`}
                className="rounded-xl border border-border/80 bg-surface-raised/30 p-4 transition-colors hover:border-border"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h5 className="font-mono text-sm font-semibold text-foreground">
                      {lead.lead_ref || `Lead ${index + 1}`}
                    </h5>
                    <p className="mt-0.5 text-xs text-muted">
                      {lead.customer_name || "Customer not identified"}
                      {lead.customer_id !== null && ` · ID ${lead.customer_id}`}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <StatusBadge
                      label={lead.matched === null ? "Match unknown" : lead.matched ? "Matched" : "Not matched"}
                      variant={lead.matched ? "positive" : "default"}
                    />
                    {lead.matched_by && <StatusBadge label={`Matched by ${lead.matched_by}`} />}
                    {lead.ambiguous && <StatusBadge label="Ambiguous" variant="warning" />}
                    <StatusBadge
                      label={
                        lead.is_new_customer === null
                          ? "New customer unknown"
                          : lead.is_new_customer
                            ? "New customer"
                            : "Existing customer"
                      }
                      variant={lead.is_new_customer ? "accent" : "default"}
                    />
                  </div>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-3">
                  <LeadSummary title="Since lead · Quotes" values={lead.quotes_since_lead} />
                  <LeadSummary title="Since lead · Orders" values={lead.orders_since_lead} />
                  <LeadSummary title="Since lead · Payments" values={lead.payments_since_lead} />
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="customer-notes-title" className="space-y-3">
        <h4 id="customer-notes-title" className="text-sm font-semibold tracking-tight text-foreground">
          Backend notes
        </h4>
        {customer.notes.length === 0 ? (
          <p className="text-xs text-muted italic">No notes.</p>
        ) : (
          <ul className="space-y-2">
            {customer.notes.map((note, index) => {
              const isWarning = /ambiguous|different|caution|mismatch|unreliable/i.test(note);
              return (
                <li
                  key={`${index}-${note}`}
                  className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-xs leading-relaxed ${
                    isWarning
                      ? "border-warning-border bg-warning-muted text-warning-foreground"
                      : "border-border/70 bg-surface-raised/40 text-foreground/80"
                  }`}
                >
                  <Info size={14} className="mt-0.5 shrink-0 opacity-70" aria-hidden="true" />
                  <span>{note}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

function SummaryCard({
  title,
  icon: Icon,
  count,
  total,
  children,
}: {
  title: string;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  count: number;
  total: number;
  children: ReactNode;
}) {
  return (
    <article className="rounded-xl border border-border/80 bg-surface-raised/40 p-4 transition-all">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</h4>
        {Icon && <Icon size={14} className="text-muted/70" />}
      </div>
      <p className="mt-2 font-mono text-2xl font-bold tracking-tight text-foreground tabular-nums">
        {count}
      </p>
      <p className="mt-1 font-mono text-xs font-medium text-foreground/80 tabular-nums">
        Total {formatAmount(total)}
      </p>
      <div className="mt-3 space-y-1.5 border-t border-border/60 pt-3 text-xs text-muted">
        {children}
      </div>
    </article>
  );
}

function CountList({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  if (entries.length === 0) return null;

  return (
    <ul className="flex flex-wrap gap-x-2.5 gap-y-1">
      {entries.map(([label, count]) => (
        <li key={label} className="inline-flex items-center gap-1 rounded bg-surface px-1.5 py-0.5 text-[11px] font-mono text-foreground/80 border border-border/50">
          <span>{label || "Unknown"}:</span>
          <span className="font-semibold text-accent">{count}</span>
        </li>
      ))}
    </ul>
  );
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function DateRange({ first, last }: { first: string | null; last: string | null }) {
  return (
    <p className="text-[11px] text-muted">
      Dates: <span className="font-mono text-foreground/80">{formatDate(first)}</span> to{" "}
      <span className="font-mono text-foreground/80">{formatDate(last)}</span>
    </p>
  );
}

function LeadSummary({ title, values }: { title: string; values: Record<string, unknown> }) {
  const entries = Object.entries(values);

  return (
    <div className="rounded-lg border border-border/60 bg-surface/80 p-3">
      <h6 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">{title}</h6>
      {entries.length === 0 ? (
        <p className="text-xs text-muted italic">No summary</p>
      ) : (
        <dl className="space-y-1.5">
          {entries.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-2 text-xs">
              <dt className="text-muted capitalize">{key.replaceAll("_", " ")}</dt>
              <dd className="font-mono text-right text-foreground font-medium">{formatUnknown(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function StatusBadge({
  label,
  variant = "default",
}: {
  label: string;
  variant?: "default" | "warning" | "positive" | "accent";
}) {
  let style = "border-border bg-surface-raised text-muted";
  if (variant === "warning") {
    style = "border-warning-border bg-warning-muted text-warning-foreground font-medium";
  } else if (variant === "positive") {
    style = "border-positive-border bg-positive-muted text-positive font-medium";
  } else if (variant === "accent") {
    style = "border-accent/40 bg-accent/15 text-accent font-medium";
  }

  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] ${style}`}>
      {label}
    </span>
  );
}

function formatAmount(amount: number) {
  const whole = Number.isInteger(amount);
  return amount.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  });
}

function formatUnknown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") {
    return /^\d{4}-\d{2}-\d{2}T/.test(value) ? formatDate(value) : value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value) ?? "—";
}
