"use client";

import Image from "next/image";
import { useState } from "react";
import { cn } from "@/lib/utils";

interface BrandLogoProps {
  className?: string;
  showSubline?: boolean;
}

export function BrandLogo({ className, showSubline = true }: BrandLogoProps) {
  const [hasError, setHasError] = useState(false);

  return (
    <div className={cn("inline-flex items-center gap-3 select-none", className)}>
      {!hasError ? (
        <div className="relative flex items-center">
          <Image
            src="/biztek-logo.png"
            alt="Biztek AI"
            width={108}
            height={36}
            priority
            onError={() => setHasError(true)}
            className="h-8 w-auto object-contain"
          />
        </div>
      ) : (
        /* Honest fallback: only active if biztek-logo.png fails to load */
        <div className="flex items-center gap-2.5">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent font-bold text-accent-foreground shadow-sm">
            B
          </div>
          <p className="text-sm font-semibold tracking-wide text-foreground">
            BIZTEK <span className="text-accent">AI</span>
          </p>
        </div>
      )}

      {showSubline && (
        <div className="flex items-center gap-2.5">
          <span className="h-3.5 w-px bg-border" aria-hidden="true" />
          <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.22em] text-muted">
            Command Center
          </span>
        </div>
      )}
    </div>
  );
}
