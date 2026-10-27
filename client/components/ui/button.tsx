import * as React from "react";
import { Button as ButtonPrimitive } from "@base-ui/react/button";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center rounded-lg border text-sm font-medium whitespace-nowrap select-none transition-all duration-fast ease-default outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 disabled:shadow-none aria-invalid:border-negative aria-invalid:ring-2 aria-invalid:ring-negative/30 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-accent text-accent-foreground font-semibold shadow-sm hover:bg-accent-hover hover:shadow-glow hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98]",
        outline:
          "border-border bg-surface text-foreground shadow-sm hover:bg-surface-raised hover:border-border-hover hover:text-foreground hover:-translate-y-0.5 active:translate-y-0",
        secondary:
          "border-border bg-surface-raised text-foreground shadow-sm hover:bg-surface-overlay hover:border-border-hover hover:-translate-y-0.5 active:translate-y-0",
        ghost:
          "border-transparent text-muted hover:bg-surface-raised hover:text-foreground active:bg-surface-overlay",
        destructive:
          "border-negative-border bg-negative-muted text-red-400 hover:bg-negative/20 hover:border-negative/50 hover:text-red-300 focus-visible:ring-negative",
        link:
          "border-transparent text-accent underline-offset-4 hover:underline active:text-accent-hover",
      },
      size: {
        default: "h-9 gap-2 px-3.5 py-2",
        xs: "h-6 gap-1 rounded-md px-2 text-xs [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1.5 rounded-md px-2.5 text-xs [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-10 gap-2.5 rounded-xl px-5 text-base [&_svg:not([class*='size-'])]:size-5",
        icon: "size-9 rounded-lg",
        "icon-xs": "size-6 rounded-md [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-7 rounded-md [&_svg:not([class*='size-'])]:size-3.5",
        "icon-lg": "size-10 rounded-xl [&_svg:not([class*='size-'])]:size-5",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
