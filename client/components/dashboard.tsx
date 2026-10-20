"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  Boxes,
  ChevronRight,
  Clock,
  DollarSign,
  FileText,
  RefreshCw,
  UserX,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useLiveData } from "@/hooks/use-live-data";
import { fetchSignals, fetchStuckLeads } from "@/services/api";
import {
  QuoteVolumeChart,
  RevenueTrendChart,
  TopRepsChart,
} from "@/components/charts";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type {
  CustomerRef,
  SignalsQuery,
  SignalsReport,
  StaleQuote,
  StalledOrder,
  StuckLead,
  StuckLeadsResponse,
  UnpaidInvoice,
} from "@/types/api";

const ROW_PAGE_SIZE = 25;

const STALE_DAY_OPTIONS = [3, 5, 7, 14, 30];
const STALLED_DAY_OPTIONS = [2, 3, 5, 7, 14];
const OVERDUE_DAY_OPTIONS = [7, 14, 30, 60, 90];

function formatMoney(value: number): string {
  const whole = Number.isInteger(value);
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  });
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
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

function selectableCustomerId(customer: CustomerRef | null): number | null {
  const id = customer?.id ?? null;
  return id !== null && id > 0 ? id : null;
}

export function Dashboard({
  actionCenter,
  onSelectCustomer,
}: {
  actionCenter: ReactNode;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const { snapshot, status, error: liveError, lastEventAt, refresh } = useLiveData();

  const [signals, setSignals] = useState<SignalsReport | null>(null);
  const [signalsError, setSignalsError] = useState("");
  const [isLoadingSignals, setIsLoadingSignals] = useState(true);
  const [filters, setFilters] = useState<SignalsQuery>({
    staleDays: 3,
    stalledDays: 2,
    overdueDays: 30,
  });
  const [activeTab, setActiveTab] = useState("overview");
  const [rowLimit, setRowLimit] = useState(ROW_PAGE_SIZE);
  const [reloadKey, setReloadKey] = useState(0);
  const [stalledOrders, setStalledOrders] = useState<StalledOrder[] | null>(null);
  const [stalledOrdersError, setStalledOrdersError] = useState("");
  const [isLoadingStalledOrders, setIsLoadingStalledOrders] = useState(false);
  const [stuckLeads, setStuckLeads] = useState<StuckLeadsResponse | null>(null);
  const [stuckLeadsError, setStuckLeadsError] = useState("");
  const [isLoadingStuckLeads, setIsLoadingStuckLeads] = useState(true);
  const [leadRowLimit, setLeadRowLimit] = useState(ROW_PAGE_SIZE);
  const stuckLeadsRequestRef = useRef<Promise<StuckLeadsResponse> | null>(null);
  const stuckLeadsLoadedForReloadRef = useRef<number | null>(null);
  const hasRequestedSignalsRef = useRef(false);
  const previousFilterKeyRef = useRef(JSON.stringify(filters));
  const previousReloadKeyRef = useRef(reloadKey);
  const previousSnapshotAtRef = useRef<string | null>(null);
  const signalsRequestRef = useRef<{
    key: string;
    promise: Promise<SignalsReport>;
  } | null>(null);

  // The first snapshot only establishes the baseline. Later generated_at
  // changes refresh signals; an in-flight request is shared across StrictMode's
  // development effect replay so the initial mount still makes one request.
  useEffect(() => {
    let isActive = true;
    const filterKey = JSON.stringify(filters);
    const filtersChanged = previousFilterKeyRef.current !== filterKey;
    previousFilterKeyRef.current = filterKey;
    const retryChanged = previousReloadKeyRef.current !== reloadKey;
    previousReloadKeyRef.current = reloadKey;
    const snapshotAt = snapshot?.generated_at ?? null;
    const snapshotChanged =
      previousSnapshotAtRef.current !== null &&
      snapshotAt !== null &&
      previousSnapshotAtRef.current !== snapshotAt;
    if (snapshotAt !== null) previousSnapshotAtRef.current = snapshotAt;

    const shouldFetch =
      !hasRequestedSignalsRef.current || filtersChanged || retryChanged || snapshotChanged;
    const requestKey = `${filterKey}:${reloadKey}`;
    const inFlight = signalsRequestRef.current;
    if (!shouldFetch && !inFlight) return;

    const request =
      inFlight?.key === requestKey
        ? inFlight.promise
        : fetchSignals({ ...filters, includeStalled: false });
    if (request !== inFlight?.promise) {
      hasRequestedSignalsRef.current = true;
      signalsRequestRef.current = { key: requestKey, promise: request };
    }

    request
      .then((report) => {
        if (!isActive) return;
        setSignals(report);
        setSignalsError("");
      })
      .catch((error: unknown) => {
        if (!isActive) return;
        setSignalsError(
          error instanceof Error ? error.message : "Could not load the signals report.",
        );
      })
      .finally(() => {
        if (signalsRequestRef.current?.promise === request) {
          signalsRequestRef.current = null;
        }
        if (isActive) setIsLoadingSignals(false);
      });

    return () => {
      isActive = false;
    };
  }, [filters, reloadKey, snapshot?.generated_at]);

  useEffect(() => {
    if (activeTab !== "pipeline") return;
    let isActive = true;

    fetchSignals({ ...filters, includeStalled: true })
      .then((report) => {
        if (!isActive) return;
        setStalledOrders(report.stalled_orders.items);
        setStalledOrdersError("");
      })
      .catch((error: unknown) => {
        if (!isActive) return;
        setStalledOrdersError(
          error instanceof Error ? error.message : "Could not load stalled orders.",
        );
      })
      .finally(() => {
        if (isActive) setIsLoadingStalledOrders(false);
      });

    return () => {
      isActive = false;
    };
  }, [activeTab, filters, reloadKey, snapshot?.generated_at]);

  // Stuck leads feed the Overview card and the Leads tab. Fetched once per
  // reload generation (StrictMode's double effect mount shares one in-flight
  // request); all state is written only after the request settles.
  useEffect(() => {
    if (activeTab !== "leads" && activeTab !== "overview") return undefined;
    const alreadyLoaded =
      stuckLeadsLoadedForReloadRef.current === reloadKey &&
      (stuckLeads !== null || stuckLeadsError !== "");
    const inFlight = stuckLeadsRequestRef.current;
    if (alreadyLoaded && !inFlight) return undefined;

    let isActive = true;
    const request = inFlight ?? fetchStuckLeads();
    stuckLeadsRequestRef.current = request;
    request
      .then((result) => {
        stuckLeadsLoadedForReloadRef.current = reloadKey;
        if (!isActive) return;
        setStuckLeads(result);
        setStuckLeadsError("");
      })
      .catch((error: unknown) => {
        stuckLeadsLoadedForReloadRef.current = reloadKey;
        if (!isActive) return;
        setStuckLeadsError(
          error instanceof Error ? error.message : "Could not load stuck leads.",
        );
      })
      .finally(() => {
        if (stuckLeadsRequestRef.current === request) {
          stuckLeadsRequestRef.current = null;
        }
        if (isActive) setIsLoadingStuckLeads(false);
      });

    return () => {
      isActive = false;
    };
  }, [activeTab, reloadKey, stuckLeads, stuckLeadsError]);

  function applyFilter(patch: SignalsQuery) {
    setIsLoadingSignals(true);
    setRowLimit(ROW_PAGE_SIZE);
    if (activeTab === "pipeline") {
      setStalledOrders(null);
      setIsLoadingStalledOrders(true);
    }
    setFilters((current) => ({ ...current, ...patch }));
  }

  function retrySignals() {
    setIsLoadingSignals(true);
    setSignalsError("");
    if (activeTab === "pipeline") {
      setStalledOrders(null);
      setStalledOrdersError("");
      setIsLoadingStalledOrders(true);
    }
    setReloadKey((current) => current + 1);
  }

  function retryStuckLeads() {
    setIsLoadingStuckLeads(true);
    setStuckLeadsError("");
    setReloadKey((current) => current + 1);
  }

  const liveLabel =
    status === "live"
      ? "Live stream connected"
      : status === "connecting"
        ? "Connecting to live stream..."
        : status === "reconnecting"
          ? "Live stream reconnecting..."
          : "Live stream offline";
  const liveDot =
    status === "live"
      ? "bg-emerald-400"
      : status === "offline"
        ? "bg-red-400"
        : "bg-amber-300";

  return (
    <section aria-labelledby="dashboard-title" className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="space-y-0.5 text-xs text-muted">
            <p>Metrics as of {snapshot ? formatDateTime(snapshot.generated_at) : "—"}</p>
            <p>Signals as of {signals ? formatDateTime(signals.generated_at) : "—"}</p>
          </div>
          <h1 id="dashboard-title" className="mt-1 text-2xl font-semibold tracking-tight">
            Command center
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-muted"
            role="status"
            title={liveError || liveLabel}
          >
            <span className={`h-2 w-2 rounded-full ${liveDot}`} aria-hidden="true" />
            {liveLabel}
          </span>
          {lastEventAt && (
            <span className="text-xs text-muted">Updated {formatDateTime(lastEventAt)}</span>
          )}
          <button
            type="button"
            onClick={() => {
              void refresh();
              retrySignals();
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
          >
            <RefreshCw size={13} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </div>

      {(liveError || signalsError) && (
        <div className="rounded-xl border border-red-400/30 bg-red-400/5 px-4 py-3 text-sm text-red-300" role="alert">
          {liveError && <p>{liveError}</p>}
          {signalsError && <p className={liveError ? "mt-1" : ""}>{signalsError}</p>}
        </div>
      )}

      <Tabs
        value={activeTab}
        onValueChange={(value) => {
          const nextTab = String(value);
          setActiveTab(nextTab);
          setRowLimit(ROW_PAGE_SIZE);
          if (nextTab === "pipeline") {
            setStalledOrders(null);
            setStalledOrdersError("");
            setIsLoadingStalledOrders(true);
          }
        }}
      >
        <TabsList aria-label="Dashboard sections" className="max-w-full flex-wrap">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="receivables">Receivables</TabsTrigger>
          <TabsTrigger value="pipeline">Pipeline</TabsTrigger>
          <TabsTrigger value="leads">Leads</TabsTrigger>
          <TabsTrigger value="ai-review">AI review</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-5">
          {!snapshot ? (
            <section className="rounded-xl border border-border bg-surface p-8 text-center">
              <Activity size={22} className="mx-auto text-accent" aria-hidden="true" />
              <p className="mt-3 text-sm font-medium">Waiting for the first dashboard snapshot</p>
              <p className="mt-1 text-xs text-muted">
                The backend builds one on its scheduler cycle (`GET /api/metrics` returns 503 until then).
              </p>
              <button
                type="button"
                onClick={() => void refresh()}
                className="mt-4 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-muted hover:bg-surface-raised hover:text-foreground"
              >
                <RefreshCw size={13} aria-hidden="true" />
                Try again
              </button>
            </section>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <KpiCard
                  icon={DollarSign}
                  label="Revenue today"
                  value={snapshot.revenue_today_display}
                  detail={`${snapshot.payments_count_today} payments received`}
                />
                <KpiCard
                  icon={Activity}
                  label="Month to date"
                  value={snapshot.revenue_month_to_date_display}
                  detail="Payments received this month"
                />
                <KpiCard
                  icon={Clock}
                  label="Last 7 days"
                  value={snapshot.revenue_last_7_days_display}
                  detail="Today plus the previous 6 days"
                />
                <KpiCard
                  icon={FileText}
                  label="Open quotes"
                  value={`${snapshot.open_quotes_count}`}
                  detail={`${snapshot.open_quotes_value_display} in open value`}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <KpiCard
                  icon={AlertTriangle}
                  label="Unpaid balance"
                  value={snapshot.unpaid_total_display}
                  detail={`${snapshot.unpaid_count} invoices with a balance`}
                />
                <KpiCard
                  icon={Boxes}
                  label="Overdue balance"
                  value={snapshot.overdue_total_display}
                  detail={`${snapshot.overdue_count} invoices past the overdue threshold`}
                />
                <KpiCard
                  icon={FileText}
                  label="Stale quotes"
                  value={`${signals?.summary.stale_quotes.count ?? "—"}`}
                  detail={
                    signals
                      ? `${formatMoney(signals.summary.stale_quotes.total_amount)} in stalled value`
                      : "Waiting for signals"
                  }
                />
                <KpiCard
                  icon={Users}
                  label="Records scanned"
                  value={`${snapshot.scanned.quotes.toLocaleString("en-US")} quotes`}
                  detail={`${snapshot.scanned.orders.toLocaleString("en-US")} orders · ${snapshot.scanned.payments.toLocaleString("en-US")} payments`}
                />
                <KpiCard
                  icon={UserX}
                  label="Stuck leads"
                  value={stuckLeads ? stuckLeads.total.toLocaleString("en-US") : "—"}
                  detail={
                    stuckLeads
                      ? `Active in the last ${stuckLeads.days} days with a description · open the Leads tab`
                      : "Waiting for stuck leads"
                  }
                  onActivate={() => {
                    setActiveTab("leads");
                    setLeadRowLimit(ROW_PAGE_SIZE);
                  }}
                />
              </div>

              <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
                <ChartCard title="Revenue received" subtitle="Last 14 days · from payments">
                  <RevenueTrendChart data={snapshot.revenue_per_day} />
                </ChartCard>
                <ChartCard title="Quotes created" subtitle="Last 14 days · from quotes">
                  <QuoteVolumeChart data={snapshot.quotes_per_day} />
                </ChartCard>
              </div>

              <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <ChartCard title="Top reps this month" subtitle="Ranked by payments received">
                  {snapshot.top_reps_this_month.length > 0 ? (
                    <TopRepsChart reps={snapshot.top_reps_this_month} />
                  ) : (
                    <EmptyNote>No payments have been recorded this month.</EmptyNote>
                  )}
                </ChartCard>

                <section className="rounded-xl border border-border bg-surface">
                  <div className="flex items-center gap-3 border-b border-border px-5 py-4">
                    <AlertTriangle size={17} className="text-accent" aria-hidden="true" />
                    <div>
                      <h2 className="font-semibold">Needs attention</h2>
                      <p className="mt-1 text-xs text-muted">
                        Deterministic counts from `GET /api/signals`
                      </p>
                    </div>
                  </div>
                  <div className="space-y-3 p-4">
                    <AttentionRow
                      label="Stale quotes"
                      count={signals?.summary.stale_quotes.count}
                      amount={
                        signals ? formatMoney(signals.summary.stale_quotes.total_amount) : undefined
                      }
                      onClick={() => {
                        setActiveTab("pipeline");
                        setRowLimit(ROW_PAGE_SIZE);
                      }}
                    />
                    <AttentionRow
                      label="Unpaid invoices"
                      count={signals?.summary.unpaid_invoices.count}
                      amount={
                        signals
                          ? formatMoney(signals.summary.unpaid_invoices.total_amount)
                          : undefined
                      }
                      detail={
                        signals
                          ? `${signals.summary.unpaid_invoices.overdue_count} overdue · ${signals.summary.unpaid_invoices.partially_paid_count} partially paid`
                          : undefined
                      }
                      onClick={() => {
                        setActiveTab("receivables");
                        setRowLimit(ROW_PAGE_SIZE);
                      }}
                    />
                    <AttentionRow
                      label="Stalled orders"
                      count={signals?.summary.stalled_orders.count}
                      amount={
                        signals ? formatMoney(signals.summary.stalled_orders.total_amount) : undefined
                      }
                      detail="Signal is marked unreliable by the backend"
                      onClick={() => {
                        setActiveTab("pipeline");
                        setRowLimit(ROW_PAGE_SIZE);
                      }}
                    />
                  </div>
                </section>
              </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="receivables">
          <FilterBar
            title="Receivables"
            description="Every invoice with a balance greater than zero. The overdue threshold changes which rows are flagged, not which rows are returned."
            filters={[
              {
                label: "Overdue after",
                value: filters.overdueDays ?? 30,
                options: OVERDUE_DAY_OPTIONS,
                suffix: "days",
                onChange: (days) => applyFilter({ overdueDays: days }),
              },
            ]}
          />
          {isLoadingSignals && !signals ? (
            <LoadingNote />
          ) : signals ? (
            <UnpaidTable
              rows={signals.unpaid_invoices}
              totalCount={signals.summary.unpaid_invoices.count}
              rowLimit={rowLimit}
              onShowMore={() => setRowLimit((current) => current + ROW_PAGE_SIZE)}
              onSelectCustomer={onSelectCustomer}
            />
          ) : (
            <ErrorNote message={signalsError || "Signals are unavailable."} onRetry={retrySignals} />
          )}
        </TabsContent>

        <TabsContent value="pipeline">
          <FilterBar
            title="Pipeline"
            description="Open quotes and stalled orders. Both lists are computed by the backend; stalled orders are explicitly unreliable because the ERP has no status timestamps."
            filters={[
              {
                label: "Quotes stale after",
                value: filters.staleDays ?? 3,
                options: STALE_DAY_OPTIONS,
                suffix: "days",
                onChange: (days) => applyFilter({ staleDays: days }),
              },
              {
                label: "Orders stalled after",
                value: filters.stalledDays ?? 2,
                options: STALLED_DAY_OPTIONS,
                suffix: "days",
                onChange: (days) => applyFilter({ stalledDays: days }),
              },
            ]}
          />
          {isLoadingSignals && !signals ? (
            <LoadingNote />
          ) : signals ? (
            <div className="space-y-5">
              <StaleQuoteTable
                rows={signals.stale_quotes}
                totalCount={signals.summary.stale_quotes.count}
                rowLimit={rowLimit}
                onShowMore={() => setRowLimit((current) => current + ROW_PAGE_SIZE)}
                onSelectCustomer={onSelectCustomer}
              />
              {isLoadingStalledOrders && !stalledOrders ? (
                <LoadingNote />
              ) : stalledOrdersError ? (
                <ErrorNote message={stalledOrdersError} onRetry={retrySignals} />
              ) : stalledOrders ? (
                <StalledOrderTable
                  rows={stalledOrders}
                  totalCount={signals.summary.stalled_orders.count}
                  reliable={signals.stalled_orders.reliable}
                  rowLimit={rowLimit}
                  onShowMore={() => setRowLimit((current) => current + ROW_PAGE_SIZE)}
                  onSelectCustomer={onSelectCustomer}
                />
              ) : null}
            </div>
          ) : (
            <ErrorNote message={signalsError || "Signals are unavailable."} onRetry={retrySignals} />
          )}
        </TabsContent>

        <TabsContent value="leads" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-4 rounded-xl border border-border bg-surface px-5 py-4">
            <div className="max-w-2xl">
              <h2 className="font-semibold">Leads</h2>
              <p className="mt-1 text-xs text-muted">
                Leads stuck before becoming an order: status New or Attempted to Contact, activity
                within the last {stuckLeads?.days ?? 14} days, a description present, not flagged
                by the lead desk. The filter and every parsed field come from the backend
                (`GET /api/leads/stuck`) - this table only renders them.
              </p>
            </div>
          </div>
          {isLoadingStuckLeads && !stuckLeads ? (
            <LoadingNote />
          ) : stuckLeads ? (
            <StuckLeadsTable
              rows={stuckLeads.items}
              totalCount={stuckLeads.total}
              rowLimit={leadRowLimit}
              onShowMore={() => setLeadRowLimit((current) => current + ROW_PAGE_SIZE)}
            />
          ) : (
            <ErrorNote
              message={stuckLeadsError || "Stuck leads are unavailable."}
              onRetry={retryStuckLeads}
            />
          )}
        </TabsContent>

        <TabsContent value="ai-review">{actionCenter}</TabsContent>
      </Tabs>

      <p className="text-[11px] text-muted">
        All totals are computed by the backend. This UI only formats and renders them.
      </p>
    </section>
  );
}

function KpiCard({
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

function ChartCard({
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

function AttentionRow({
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

function FilterBar({
  title,
  description,
  filters,
}: {
  title: string;
  description: string;
  filters: {
    label: string;
    value: number;
    options: number[];
    suffix: string;
    onChange: (value: number) => void;
  }[];
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

function TableShell({
  title,
  icon: Icon,
  count,
  totalCount,
  warning,
  children,
}: {
  title: string;
  icon: LucideIcon;
  count: number;
  totalCount: number;
  warning?: string;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <Icon size={16} className="text-muted" aria-hidden="true" />
          <h2 className="text-sm font-semibold">{title}</h2>
        </div>
        <span className="text-xs text-muted">
          Showing {Math.min(count, totalCount).toLocaleString("en-US")} of{" "}
          {totalCount.toLocaleString("en-US")}
        </span>
      </div>
      {warning && (
        <p className="border-b border-amber-400/20 bg-amber-400/5 px-4 py-2 text-xs text-amber-300">
          {warning}
        </p>
      )}
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}

function ShowMore({
  shown,
  total,
  onShowMore,
}: {
  shown: number;
  total: number;
  onShowMore: () => void;
}) {
  if (shown >= total) return null;
  return (
    <div className="flex justify-center border-t border-border px-4 py-3">
      <button
        type="button"
        onClick={onShowMore}
        className="rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
      >
        Show {Math.min(ROW_PAGE_SIZE, total - shown)} more
      </button>
    </div>
  );
}

function CustomerCell({
  customer,
  onSelectCustomer,
}: {
  customer: CustomerRef | null;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const customerId = selectableCustomerId(customer);
  return (
    <div>
      <p>{customer?.company_name || "Unknown customer"}</p>
      {customerId ? (
        <button
          type="button"
          onClick={() => onSelectCustomer(customerId)}
          className="mt-0.5 text-xs text-accent transition-colors hover:text-accent/80"
        >
          View context
        </button>
      ) : (
        <p className="mt-0.5 text-xs text-muted">No customer ID</p>
      )}
    </div>
  );
}

function UnpaidTable({
  rows,
  totalCount,
  rowLimit,
  onShowMore,
  onSelectCustomer,
}: {
  rows: UnpaidInvoice[];
  totalCount: number;
  rowLimit: number;
  onShowMore: () => void;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const visible = rows.slice(0, rowLimit);
  return (
    <TableShell
      title="Unpaid invoices"
      icon={AlertTriangle}
      count={visible.length}
      totalCount={totalCount}
    >
      {rows.length === 0 ? (
        <EmptyNote>No invoices currently carry a balance.</EmptyNote>
      ) : (
        <>
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Invoice</th>
                <th className="px-4 py-3 font-medium">Customer</th>
                <th className="px-4 py-3 font-medium">Payment status</th>
                <th className="px-4 py-3 text-right font-medium">Balance</th>
                <th className="px-4 py-3 text-right font-medium">Total</th>
                <th className="px-4 py-3 text-right font-medium">Age</th>
                <th className="px-4 py-3 font-medium">Overdue</th>
                <th className="px-4 py-3 font-medium">Rep</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visible.map((invoice, index) => (
                <tr key={invoice.id ?? `${invoice.inv_ref ?? "invoice"}-${index}`}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{invoice.inv_ref || "—"}</p>
                    <p className="mt-1 text-xs text-muted">ID {invoice.id ?? "—"}</p>
                  </td>
                  <td className="px-4 py-3">
                    <CustomerCell customer={invoice.customer} onSelectCustomer={onSelectCustomer} />
                  </td>
                  <td className="px-4 py-3 text-muted">
                    {invoice.payment_status_label || "Status not provided"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatMoney(invoice.balance)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {formatMoney(invoice.total_amount)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {invoice.days_invoice_age}d
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-md px-2 py-1 text-[11px] ${
                        invoice.overdue
                          ? "bg-red-400/10 text-red-300"
                          : "bg-surface-raised text-muted"
                      }`}
                    >
                      {invoice.overdue ? "Overdue" : "Within terms"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted">{invoice.sale_rep?.name || "Unassigned"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ShowMore shown={visible.length} total={totalCount} onShowMore={onShowMore} />
        </>
      )}
    </TableShell>
  );
}

function StaleQuoteTable({
  rows,
  totalCount,
  rowLimit,
  onShowMore,
  onSelectCustomer,
}: {
  rows: StaleQuote[];
  totalCount: number;
  rowLimit: number;
  onShowMore: () => void;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const visible = rows.slice(0, rowLimit);
  return (
    <TableShell
      title="Stale quotes"
      icon={FileText}
      count={visible.length}
      totalCount={totalCount}
    >
      {rows.length === 0 ? (
        <EmptyNote>No open quotes match the current stale threshold.</EmptyNote>
      ) : (
        <>
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Quote</th>
                <th className="px-4 py-3 font-medium">Customer</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Amount</th>
                <th className="px-4 py-3 text-right font-medium">Stale</th>
                <th className="px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3 font-medium">Rep</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visible.map((quote, index) => (
                <tr key={quote.id ?? `${quote.ref ?? "quote"}-${index}`}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{quote.ref || "—"}</p>
                    <p className="mt-1 text-xs text-muted">ID {quote.id ?? "—"}</p>
                  </td>
                  <td className="px-4 py-3">
                    <CustomerCell customer={quote.customer} onSelectCustomer={onSelectCustomer} />
                  </td>
                  <td className="px-4 py-3 text-muted">{quote.status || "Unknown"}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{formatMoney(quote.amount)}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {quote.days_stale}d
                  </td>
                  <td className="px-4 py-3 text-muted">{formatDate(quote.created_at)}</td>
                  <td className="px-4 py-3 text-muted">{quote.sale_rep?.name || "Unassigned"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ShowMore shown={visible.length} total={totalCount} onShowMore={onShowMore} />
        </>
      )}
    </TableShell>
  );
}

function StalledOrderTable({
  rows,
  totalCount,
  reliable,
  rowLimit,
  onShowMore,
  onSelectCustomer,
}: {
  rows: StalledOrder[];
  totalCount: number;
  reliable: boolean;
  rowLimit: number;
  onShowMore: () => void;
  onSelectCustomer: (customerId: number | null) => void;
}) {
  const visible = rows.slice(0, rowLimit);
  return (
    <TableShell
      title="Stalled orders"
      icon={Boxes}
      count={visible.length}
      totalCount={totalCount}
      warning={
        !reliable
          ? "The backend marks this signal unreliable: the ERP exposes no order status timestamps, so these rows are derived from invoice age only. Do not use them for decisions."
          : undefined
      }
    >
      {rows.length === 0 ? (
        <EmptyNote>No orders match the current stalled threshold.</EmptyNote>
      ) : (
        <>
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Order</th>
                <th className="px-4 py-3 font-medium">Customer</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Amount</th>
                <th className="px-4 py-3 text-right font-medium">Invoice age</th>
                <th className="px-4 py-3 font-medium">Invoice date</th>
                <th className="px-4 py-3 font-medium">Rep</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visible.map((order, index) => (
                <tr key={order.id ?? `${order.inv_ref ?? "order"}-${index}`}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{order.inv_ref || "—"}</p>
                    <p className="mt-1 text-xs text-muted">ID {order.id ?? "—"}</p>
                  </td>
                  <td className="px-4 py-3">
                    <CustomerCell customer={order.customer} onSelectCustomer={onSelectCustomer} />
                  </td>
                  <td className="px-4 py-3 text-muted">
                    {order.status_label || "Unknown"} ·{" "}
                    {order.payment_status_label || "Payment unknown"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatMoney(order.total_amount)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {order.days_invoice_age}d
                  </td>
                  <td className="px-4 py-3 text-muted">{formatDate(order.invoice_date)}</td>
                  <td className="px-4 py-3 text-muted">{order.sale_rep?.name || "Unassigned"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ShowMore shown={visible.length} total={totalCount} onShowMore={onShowMore} />
        </>
      )}
    </TableShell>
  );
}

function artworkLabel(present: boolean | null): string {
  if (present === true) return "Provided";
  if (present === false) return "Missing";
  return "Not stated";
}

function StuckLeadsTable({
  rows,
  totalCount,
  rowLimit,
  onShowMore,
}: {
  rows: StuckLead[];
  totalCount: number;
  rowLimit: number;
  onShowMore: () => void;
}) {
  const visible = rows.slice(0, rowLimit);
  return (
    <TableShell
      title="Stuck leads"
      icon={UserX}
      count={visible.length}
      totalCount={totalCount}
    >
      {rows.length === 0 ? (
        <EmptyNote>No leads match the stuck-lead filter right now.</EmptyNote>
      ) : (
        <>
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-4 py-3 font-medium">Lead</th>
                <th className="px-4 py-3 font-medium">Company</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Last activity</th>
                <th className="px-4 py-3 font-medium">Product</th>
                <th className="px-4 py-3 font-medium">Missing</th>
                <th className="px-4 py-3 font-medium">Artwork</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visible.map((lead) => (
                <tr key={lead.lead_id}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{lead.contact_name || "Name not provided"}</p>
                    <p className="mt-1 text-xs text-muted">{lead.source || "Unknown source"}</p>
                  </td>
                  <td className="px-4 py-3 text-muted">{lead.company || "—"}</td>
                  <td className="px-4 py-3 text-muted">{lead.status}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted">
                    {lead.days_since_activity}d
                  </td>
                  <td className="px-4 py-3">{lead.product || lead.type || "—"}</td>
                  <td className="px-4 py-3">
                    {lead.missing.length === 0 ? (
                      <span className="text-xs text-muted">Nothing stated missing</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {lead.missing.map((field) => (
                          <span
                            key={field}
                            className="rounded-md bg-amber-400/10 px-2 py-0.5 text-[11px] text-amber-300"
                          >
                            {field.replaceAll("_", " ")}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-md px-2 py-1 text-[11px] ${
                        lead.artwork_present === true
                          ? "bg-emerald-400/10 text-emerald-300"
                          : lead.artwork_present === false
                            ? "bg-amber-400/10 text-amber-300"
                            : "bg-surface-raised text-muted"
                      }`}
                    >
                      {artworkLabel(lead.artwork_present)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <ShowMore shown={visible.length} total={totalCount} onShowMore={onShowMore} />
        </>
      )}
    </TableShell>
  );
}

function LoadingNote() {
  return (
    <p className="rounded-xl border border-border bg-surface px-4 py-6 text-center text-sm text-muted" role="status">
      Loading signals from the backend...
    </p>
  );
}

function ErrorNote({ message, onRetry }: { message: string; onRetry: () => void }) {
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

function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="px-4 py-8 text-center text-xs text-muted">{children}</p>;
}
