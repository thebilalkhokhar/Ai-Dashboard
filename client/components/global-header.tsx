"use client";

import * as React from "react";
import { RefreshCw, Users } from "lucide-react";
import { BrandLogo } from "@/components/brand-logo";
import { SystemHealthBadge } from "@/components/system-health";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

interface GlobalHeaderProps {
  liveStatus: "live" | "connecting" | "reconnecting" | "offline";
  liveError?: string;
  lastEventAt?: string | null;
  onRefresh: () => void | Promise<void>;
  onOpenCustomerContext: () => void;
  className?: string;
}

export function GlobalHeader({
  liveStatus,
  liveError,
  lastEventAt,
  onRefresh,
  onOpenCustomerContext,
  className,
}: GlobalHeaderProps) {
  const [isRefreshing, setIsRefreshing] = React.useState(false);

  async function handleRefresh() {
    setIsRefreshing(true);
    try {
      await onRefresh();
    } finally {
      // Small visual feedback window
      setTimeout(() => setIsRefreshing(false), 300);
    }
  }

  const liveLabel =
    liveStatus === "live"
      ? "Live stream connected"
      : liveStatus === "connecting"
        ? "Connecting to live stream..."
        : liveStatus === "reconnecting"
          ? "Live stream reconnecting..."
          : "Live stream offline";

  const liveDotClass =
    liveStatus === "live"
      ? "bg-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.5)]"
      : liveStatus === "offline"
        ? "bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.5)]"
        : "bg-amber-300 shadow-[0_0_8px_rgba(245,158,11,0.5)]";

  return (
    <header
      data-slot="global-header"
      className={cn(
        "sticky top-0 z-40 w-full border-b border-border bg-background/90 backdrop-blur-md",
        "transition-colors duration-normal ease-default",
        className
      )}
    >
      <div className="mx-auto flex h-16 w-full max-w-[1920px] items-center justify-between gap-4 px-4 xl:px-8">
        {/* Left: Brand logo lockup */}
        <div className="flex items-center">
          <BrandLogo />
        </div>

        {/* Right: Status indicators and primary actions */}
        <div className="flex flex-wrap items-center gap-2.5 sm:gap-3">
          {/* Backend health */}
          <div className="hidden sm:flex items-center">
            <SystemHealthBadge />
          </div>

          {/* Live stream status */}
          <div
            className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-foreground/90 transition-all duration-fast hover:border-border-hover hover:bg-surface-raised cursor-default select-none shadow-sm"
            role="status"
            title={liveError || liveLabel}
          >
            <span
              className={cn("h-1.5 w-1.5 rounded-full transition-colors", liveDotClass)}
              aria-hidden="true"
            />
            <span className="hidden md:inline">{liveLabel}</span>
            <span className="md:hidden">
              {liveStatus === "live" ? "Live" : "Offline"}
            </span>
          </div>

          {/* Last updated timestamp */}
          {lastEventAt && (
            <span className="hidden lg:inline text-xs font-mono text-muted select-none">
              Updated {formatDateTime(lastEventAt)}
            </span>
          )}

          {/* Refresh action */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleRefresh()}
            className="gap-1.5 font-normal text-muted hover:text-foreground text-xs"
            title="Refresh dashboard snapshot and signals"
          >
            <RefreshCw
              size={13}
              className={cn("transition-transform duration-normal", isRefreshing && "animate-spin text-accent")}
              aria-hidden="true"
            />
            <span>Refresh</span>
          </Button>

          {/* Customer context entry point */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onOpenCustomerContext}
            className="gap-1.5 font-normal border-border hover:border-accent-border hover:text-accent text-xs"
          >
            <Users size={13} aria-hidden="true" />
            <span>Customer context</span>
          </Button>
        </div>
      </div>
    </header>
  );
}
