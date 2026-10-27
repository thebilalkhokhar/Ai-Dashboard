import * as React from "react";
import { cn } from "@/lib/utils";

function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-slot="skeleton"
      className={cn(
        "animate-pulse rounded-md bg-surface-raised/80 border border-border/40",
        className
      )}
      {...props}
    />
  );
}

/**
 * Skeleton shaped like a dashboard KPI tile:
 * icon corner, label, large numeric value, and detail subline.
 */
function SkeletonKpiTile({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-surface p-5 shadow-card space-y-3",
        className
      )}
    >
      <div className="flex items-center justify-between">
        <Skeleton className="h-3.5 w-24" />
        <Skeleton className="h-4 w-4 rounded-full" />
      </div>
      <Skeleton className="h-8 w-36 font-mono" />
      <Skeleton className="h-3 w-44" />
    </div>
  );
}

/**
 * Skeleton shaped like a data table row with readable column rhythm.
 */
function SkeletonTableRow({
  columns = 5,
  className,
}: {
  columns?: number;
  className?: string;
}) {
  return (
    <tr className={cn("h-12 border-b border-border/50", className)}>
      {Array.from({ length: columns }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <Skeleton
            className={cn(
              "h-4",
              i === 0 && "w-28",
              i === 1 && "w-36",
              i === 2 && "w-20",
              i === 3 && "w-24",
              i >= 4 && "w-16"
            )}
          />
        </td>
      ))}
    </tr>
  );
}

/**
 * Skeleton shaped like a chart frame with a header, simulated plot area, and axis lines.
 */
function SkeletonChartFrame({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-surface p-5 shadow-card space-y-4",
        className
      )}
    >
      <div className="space-y-1.5">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-3 w-48" />
      </div>
      <div className="relative h-48 w-full flex items-end gap-3 pt-6 pb-2 px-2 border-b border-l border-border/50">
        <Skeleton className="h-[35%] flex-1 rounded-t-sm" />
        <Skeleton className="h-[60%] flex-1 rounded-t-sm" />
        <Skeleton className="h-[45%] flex-1 rounded-t-sm" />
        <Skeleton className="h-[80%] flex-1 rounded-t-sm" />
        <Skeleton className="h-[55%] flex-1 rounded-t-sm" />
        <Skeleton className="h-[90%] flex-1 rounded-t-sm" />
        <Skeleton className="h-[70%] flex-1 rounded-t-sm" />
      </div>
      <div className="flex justify-between px-1">
        <Skeleton className="h-2.5 w-10" />
        <Skeleton className="h-2.5 w-10" />
        <Skeleton className="h-2.5 w-10" />
        <Skeleton className="h-2.5 w-10" />
      </div>
    </div>
  );
}

export { Skeleton, SkeletonKpiTile, SkeletonTableRow, SkeletonChartFrame };
