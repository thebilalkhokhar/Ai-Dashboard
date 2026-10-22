"use client";

import { MessageSquareText } from "lucide-react";
import { useState } from "react";
import { AgentReview } from "@/components/approval-inbox";
import { Dashboard } from "@/components/dashboard";
import { CustomerContextPanel } from "@/components/customer-context";
import { AISidebar } from "@/components/ai-sidebar";
import { SystemHealthBadge, SystemHealthProvider } from "@/components/system-health";
import { LiveDataProvider } from "@/hooks/use-live-data";
import {
  ExecutionControllerProvider,
} from "@/components/command-center/execution-context";
import { ExecutionOverlay } from "@/components/command-center/execution-overlay";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export default function Home() {
  const [isCustomerSheetOpen, setIsCustomerSheetOpen] = useState(false);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [selectedCustomerId, setSelectedCustomerId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState("command-center");
  // The Command Center has its own primary conversation. Showing the global AI
  // sidebar at the same time would duplicate that single conversation.
  const showAssistantSidebar = activeTab !== "command-center";

  function openCustomerContext(customerId: number | null) {
    setSelectedCustomerId(customerId);
    setIsCustomerSheetOpen(true);
  }

  return (
    <SystemHealthProvider>
      <LiveDataProvider>
        <ExecutionControllerProvider>
        <Sheet open={isCustomerSheetOpen} onOpenChange={setIsCustomerSheetOpen}>
          <main className="mx-auto w-full max-w-[1920px] px-4 xl:px-8">
            <div
              className={`grid transition-all duration-300 ease-in-out ${
                !showAssistantSidebar
                  ? "gap-2 xl:grid-cols-[minmax(0,1fr)]"
                  : isSidebarOpen
                    ? "gap-6 xl:grid-cols-[minmax(0,1fr)_400px]"
                    : "gap-2 xl:grid-cols-[minmax(0,1fr)_40px]"
              }`}
            >
              <div className="min-w-0 pt-6">
                <header className="mb-8 flex items-center justify-between border-b border-border pb-5">
                  <div className="flex items-center gap-3">
                    <div className="grid h-9 w-9 place-items-center rounded-lg bg-accent font-bold text-[#11150b]">B</div>
                    <div>
                      <p className="text-sm font-semibold tracking-wide">BIZTEK <span className="text-accent">AI</span></p>
                      <p className="text-[11px] text-muted">COMMAND CENTER</p>
                    </div>
                  </div>
                  <div className="hidden sm:block">
                    <SystemHealthBadge />
                  </div>
                  <div className="flex items-center gap-2">
                    <SheetTrigger
                      type="button"
                      onClick={() => setSelectedCustomerId(null)}
                      className="rounded-lg border border-border px-3 py-2 text-sm text-muted transition-colors hover:border-accent/40 hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                    >
                      Customer context
                    </SheetTrigger>
                  </div>
                </header>

                <Dashboard
                  actionCenter={<AgentReview />}
                  onSelectCustomer={openCustomerContext}
                  onTabChange={setActiveTab}
                />
              </div>
              {showAssistantSidebar && (
                <div id="ai-sidebar" className="hidden min-w-0 xl:block">
                  <AISidebar
                    isOpen={isSidebarOpen}
                    onOpen={() => setIsSidebarOpen(true)}
                    onClose={() => setIsSidebarOpen(false)}
                  />
                </div>
              )}
            </div>
          </main>
          <SheetContent side="right" className="sm:max-w-[600px] xl:max-w-[800px]">
            <SheetHeader className="border-b border-border pb-4">
              <SheetTitle>Customer context</SheetTitle>
              <p className="text-sm text-muted">Account, activity, and lead details</p>
            </SheetHeader>
            <CustomerContextPanel
              key={selectedCustomerId ?? "customer-search"}
              selectedCustomerId={selectedCustomerId}
            />
          </SheetContent>
        </Sheet>
        <Sheet open={isMobileSidebarOpen} onOpenChange={setIsMobileSidebarOpen}>
          <SheetContent side="right" className="gap-0 p-0 sm:max-w-[440px] xl:hidden">
            <SheetHeader className="sr-only">
              <SheetTitle>Biztek Assistant</SheetTitle>
            </SheetHeader>
            <AISidebar
              isOpen
              onOpen={() => setIsMobileSidebarOpen(true)}
              onClose={() => setIsMobileSidebarOpen(false)}
            />
          </SheetContent>
        </Sheet>
        {showAssistantSidebar && (
          <button
            type="button"
            onClick={() => setIsMobileSidebarOpen(true)}
            className="fixed bottom-5 right-5 z-40 inline-flex items-center gap-2 rounded-full bg-accent px-4 py-3 text-sm font-semibold text-[#11150b] shadow-lg xl:hidden"
            aria-label="Open chat and voice panel"
          >
            <MessageSquareText size={17} aria-hidden="true" />
            Chat
          </button>
        )}
          {/* One overlay for every chat entry point */}
          <ExecutionOverlay />
        </ExecutionControllerProvider>
      </LiveDataProvider>
    </SystemHealthProvider>
  );
}
