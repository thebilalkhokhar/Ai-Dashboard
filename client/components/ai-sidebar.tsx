"use client";

import { Bot, MessageSquareText, Mic, PanelLeftOpen, PanelRightClose } from "lucide-react";
import { ChatInterface } from "@/components/chat-interface";
import { VoiceConsole } from "@/components/voice-console";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function AISidebar({
  isOpen,
  onOpen,
  onClose,
}: {
  isOpen: boolean;
  onOpen: () => void;
  onClose: () => void;
}) {
  if (!isOpen) {
    return (
      <aside className="sticky top-0 flex h-screen justify-center border-l border-border bg-background py-4">
        <button
          type="button"
          onClick={onOpen}
          aria-label="Open AI sidebar"
          className="grid h-9 w-9 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-raised hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <PanelLeftOpen size={18} aria-hidden="true" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="sticky top-0 flex h-screen min-h-0 flex-col overflow-hidden border-l border-border bg-background">
      <header className="flex shrink-0 items-center justify-between border-b border-border px-5 py-4">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-accent/10 text-accent">
            <Bot size={18} aria-hidden="true" />
          </span>
          <div>
            <h2 className="text-sm font-semibold">Biztek Assistant</h2>
            <p className="mt-0.5 text-xs text-muted">AI command center</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close AI sidebar"
          className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-raised hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <PanelRightClose size={16} aria-hidden="true" />
        </button>
      </header>

      <Tabs
        defaultValue="text-chat"
        className="min-h-0 flex-1 gap-0"
      >
        <TabsList aria-label="AI modes" className="mx-4 mt-4 w-auto">
          <TabsTrigger value="text-chat" className="flex-1 gap-2">
            <MessageSquareText size={15} aria-hidden="true" />
            Text Chat
          </TabsTrigger>
          <TabsTrigger value="voice-mode" className="flex-1 gap-2">
            <Mic size={15} aria-hidden="true" />
            Voice Mode
          </TabsTrigger>
        </TabsList>

        <TabsContent value="text-chat" keepMounted className="min-h-0 flex-1">
          <ChatInterface />
        </TabsContent>
        <TabsContent value="voice-mode" keepMounted className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <VoiceConsole />
        </TabsContent>
      </Tabs>
    </aside>
  );
}
