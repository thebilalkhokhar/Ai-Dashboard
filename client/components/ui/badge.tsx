import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
  {
    variants: {
      variant: {
        default:
          "border border-accent-border bg-accent-muted text-accent font-semibold",
        secondary:
          "border border-border bg-surface-raised text-foreground hover:bg-surface-overlay",
        outline:
          "border border-border text-muted bg-transparent hover:border-border-hover hover:text-foreground",
        positive:
          "border border-positive-border bg-positive-muted text-emerald-400 font-medium",
        warning:
          "border border-warning-border bg-warning-muted text-amber-300 font-medium",
        negative:
          "border border-negative-border bg-negative-muted text-red-400 font-medium",
        info:
          "border border-info-border bg-info-muted text-sky-400 font-medium",
      },
      size: {
        default: "text-xs px-2.5 py-1",
        sm: "text-[11px] px-2 py-0.5",
        lg: "text-xs px-3 py-1.5",
      },
    },
    defaultVariants: {
      variant: "secondary",
      size: "default",
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {
  dot?: boolean;
  pulse?: boolean;
}

function Badge({
  className,
  variant,
  size,
  dot,
  pulse,
  children,
  ...props
}: BadgeProps) {
  const dotColor =
    variant === "positive"
      ? "bg-emerald-400"
      : variant === "warning"
        ? "bg-amber-400"
        : variant === "negative"
          ? "bg-red-400"
          : variant === "info"
            ? "bg-sky-400"
            : variant === "default"
              ? "bg-accent"
              : "bg-muted";

  return (
    <div
      data-slot="badge"
      className={cn(badgeVariants({ variant, size }), className)}
      {...props}
    >
      {dot && (
        <span className="relative flex h-2 w-2 items-center justify-center" aria-hidden="true">
          {pulse && (
            <span
              className={cn(
                "absolute inline-flex h-full w-full animate-ping rounded-full opacity-75",
                dotColor
              )}
            />
          )}
          <span className={cn("relative inline-flex h-1.5 w-1.5 rounded-full", dotColor)} />
        </span>
      )}
      {children}
    </div>
  );
}

export { Badge, badgeVariants };
