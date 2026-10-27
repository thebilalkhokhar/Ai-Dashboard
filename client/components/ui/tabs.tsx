"use client";

import * as React from "react";
import { Tabs as TabsPrimitive } from "@base-ui/react/tabs";
import { cn } from "@/lib/utils";

function Tabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-5", className)}
      {...props}
    />
  );
}

function TabsList({ className, children, ...props }: TabsPrimitive.List.Props) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        "relative inline-flex w-fit items-center gap-1 rounded-xl border border-border bg-surface-sunken p-1 text-muted shadow-sm",
        className
      )}
      {...props}
    >
      <TabsPrimitive.Indicator
        className="absolute z-0 rounded-lg bg-surface-raised border border-border/80 shadow-sm transition-all duration-normal ease-default pointer-events-none data-[orientation=horizontal]:top-1 data-[orientation=horizontal]:h-[calc(100%-8px)]"
        aria-hidden="true"
      />
      {children}
    </TabsPrimitive.List>
  );
}

function TabsTrigger({ className, children, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-trigger"
      className={cn(
        "relative z-10 inline-flex h-9 items-center justify-center rounded-lg px-3.5 text-xs sm:text-sm font-medium transition-colors duration-fast select-none cursor-pointer",
        "text-muted hover:text-foreground hover:bg-surface-raised/50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        "data-[active]:text-foreground data-[active]:font-semibold",
        className
      )}
      {...props}
    >
      {children}
    </TabsPrimitive.Tab>
  );
}

function TabsContent({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="tabs-content"
      className={cn("min-w-0 outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}
      {...props}
    />
  );
}

const TabsIndicator = TabsPrimitive.Indicator;

export { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator };
