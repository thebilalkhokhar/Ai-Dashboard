"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Building2, Mail, Phone, Search } from "lucide-react";
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
      className="overflow-hidden rounded-xl border border-border bg-surface"
    >
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 id="customer-context-title" className="font-semibold">Customer context</h2>
          <p className="mt-1 text-xs text-muted">
            Look up by the backend customer ID. There is no customer search endpoint.
          </p>
        </div>
        <form onSubmit={handleSubmit} className="flex w-full gap-2 sm:w-auto">
          <label className="sr-only" htmlFor="customer-id">Customer ID</label>
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
            className="min-w-0 flex-1 rounded-lg border border-border bg-[#0c0f15] px-3 py-2 text-sm outline-none placeholder:text-muted focus-visible:border-ring sm:w-40"
          />
          <button
            type="submit"
            disabled={isLoading}
            className="inline-flex items-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-[#11150b] transition-opacity hover:opacity-90 disabled:cursor-wait disabled:opacity-60"
          >
            <Search size={15} aria-hidden="true" />
            {isLoading ? "Loading..." : "Load"}
          </button>
        </form>
      </div>

      <div className="p-5">
        {errorMessage && <p className="mb-4 text-sm text-red-400" role="alert">{errorMessage}</p>}
        {isLoading ? (
          <p className="text-sm text-muted" role="status">Loading customer context...</p>
        ) : customer ? (
          <CustomerDetails customer={customer} />
        ) : (
          <p className="text-sm text-muted">Enter a customer ID to view its account and lead context.</p>
        )}
      </div>
    </section>
  );
}

function CustomerDetails({ customer }: { customer: CustomerContext }) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-lg bg-surface-raised text-accent">
            <Building2 size={18} aria-hidden="true" />
          </span>
          <div>
            <h3 className="font-semibold">{customer.company_name || `Customer ${customer.customer_id}`}</h3>
            <p className="mt-1 text-xs text-muted">Customer ID {customer.customer_id}</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted">
          <span className="inline-flex items-center gap-1.5">
            <Mail size={13} aria-hidden="true" />
            {customer.contact.email || "No email"}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Phone size={13} aria-hidden="true" />
            {customer.contact.phone || "No phone"}
          </span>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <SummaryCard title="Quotes" count={customer.quotes.count} total={customer.quotes.total_amount}>
          <CountList counts={customer.quotes.status_counts} />
          <DateRange first={customer.quotes.first_at} last={customer.quotes.last_at} />
        </SummaryCard>
        <SummaryCard title="Orders" count={customer.orders.count} total={customer.orders.total_amount}>
          <p>Paid {formatAmount(customer.orders.amount_paid)} · Balance {formatAmount(customer.orders.balance)}</p>
          <p>Unpaid {customer.orders.unpaid_count} · Partial {customer.orders.partially_paid_count} · Paid {customer.orders.fully_paid_count}</p>
          <CountList counts={customer.orders.payment_status_counts} />
          <DateRange first={customer.orders.first_at} last={customer.orders.last_at} />
        </SummaryCard>
        <SummaryCard title="Payments" count={customer.payments.count} total={customer.payments.total_amount}>
          <DateRange first={customer.payments.first_at} last={customer.payments.last_at} />
        </SummaryCard>
      </div>

      <section aria-labelledby="customer-leads-title">
        <div className="mb-3 flex items-center justify-between">
          <h4 id="customer-leads-title" className="text-sm font-medium">Lead matches</h4>
          <span className="text-xs text-muted">{customer.leads.length} records</span>
        </div>
        {customer.leads.length === 0 ? (
          <p className="rounded-lg border border-border bg-[#0c0f15] p-4 text-sm text-muted">
            No matching lead outcomes were returned.
          </p>
        ) : (
          <div className="space-y-3">
            {customer.leads.map((lead, index) => (
              <article
                key={`${lead.lead_ref ?? "lead"}-${index}`}
                className="rounded-lg border border-border bg-[#0c0f15] p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h5 className="text-sm font-medium">{lead.lead_ref || `Lead ${index + 1}`}</h5>
                    <p className="mt-1 text-xs text-muted">
                      {lead.customer_name || "Customer not identified"}
                      {lead.customer_id !== null && ` · ID ${lead.customer_id}`}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge label={lead.matched === null ? "Match unknown" : lead.matched ? "Matched" : "Not matched"} />
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

      <section aria-labelledby="customer-notes-title">
        <h4 id="customer-notes-title" className="mb-2 text-sm font-medium">Backend notes</h4>
        {customer.notes.length === 0 ? (
          <p className="text-xs text-muted">No notes.</p>
        ) : (
          <ul className="space-y-2">
            {customer.notes.map((note, index) => (
              <li key={`${index}-${note}`} className="rounded-lg border border-border bg-[#0c0f15] px-3 py-2 text-sm text-muted">
                {note}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function SummaryCard({
  title,
  count,
  total,
  children,
}: {
  title: string;
  count: number;
  total: number;
  children: ReactNode;
}) {
  return (
    <article className="rounded-lg border border-border bg-[#0c0f15] p-4">
      <h4 className="text-xs font-medium text-muted">{title}</h4>
      <p className="mt-2 text-xl font-semibold">{count}</p>
      <p className="mt-1 text-xs text-muted">Total {formatAmount(total)}</p>
      <div className="mt-3 space-y-1.5 text-xs text-muted">{children}</div>
    </article>
  );
}

function CountList({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  if (entries.length === 0) return null;

  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1">
      {entries.map(([label, count]) => <li key={label}>{label || "Unknown"}: {count}</li>)}
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
  return <p>Dates: {formatDate(first)} to {formatDate(last)}</p>;
}

function LeadSummary({ title, values }: { title: string; values: Record<string, unknown> }) {
  const entries = Object.entries(values);

  return (
    <div className="rounded-md border border-border bg-surface p-3">
      <h6 className="mb-2 text-[11px] font-medium text-muted">{title}</h6>
      {entries.length === 0 ? (
        <p className="text-xs text-muted">No summary</p>
      ) : (
        <dl className="space-y-1.5">
          {entries.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-2 text-xs">
              <dt className="text-muted">{key.replaceAll("_", " ")}</dt>
              <dd className="text-right">{formatUnknown(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function StatusBadge({ label, variant = "default" }: { label: string; variant?: "default" | "warning" }) {
  return (
    <span
      className={`rounded-md border px-2 py-1 text-[11px] ${
        variant === "warning"
          ? "border-amber-400/30 bg-amber-400/10 text-amber-300"
          : "border-border bg-surface-raised text-muted"
      }`}
    >
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
