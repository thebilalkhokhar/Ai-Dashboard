"use client";

// One controller for the whole assistant experience.
//
// It owns:
//   * the single conversation (messages, sending state, active tool, error),
//   * the current turn (the user's request, the backend task id, the phase),
//   * the one task-event stream for the current/last turn,
//   * the registered agent list used by the network and the overlay.
//
// Both chat entry points render the same messages and call the same `send`,
// so a request can never be submitted twice or appear in two conversations.
// A conversation is identified by the chat API's thread_id; each turn is a
// separate task id announced by the backend. This file never derives one from
// the other.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  fetchChatThread,
  getChatThreadId,
  sendMessage,
  setChatThreadId,
  startNewChatThread,
} from "@/services/api";
import { useAgents } from "@/hooks/use-agents";
import { useTaskEvents } from "@/hooks/use-task-events";
import type { ChatStreamEvent, ChatToolResultEvent, RegistryAgent } from "@/types/api";
import type { CommandCenterState, ExecutionPhase } from "@/lib/task-events";
import { deriveExecution, isLivePhase } from "@/lib/task-events";

// How long a finished overlay stays mounted for its exit transition. This is an
// animation duration, not a wait for work: the response is already complete.
export const EXIT_TRANSITION_MS = 650;

// What the chat stream itself can attest to. The full execution state machine
// (idle -> starting -> running -> synthesizing -> completed/failed/cancelled)
// is derived from this plus the task's persisted events by `deriveExecution`.
// A turn never holds a "cancelled" phase of its own: closing this view does not
// stop backend work, and no cancel endpoint exists, so cancellation can only
// ever come from a real task.cancelled event.
export type TurnPhase = "starting" | "synthesizing" | "completed" | "failed" | "cancelled";

export interface ActiveTurn {
  turnId: string;
  prompt: string;
  taskId: string | null;
  phase: TurnPhase;
  message: string;
  startedAt: number;
}
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface ExecutionController {
  /** The turn the overlay is showing (null when there is no visible overlay). */
  turn: ActiveTurn | null;
  /** The most recent turn, kept for the operation panel after the overlay closes. */
  lastTurn: ActiveTurn | null;
  /** Derived state machine for whatever turn is on screen. */
  execution: ExecutionPhase;
  /** True while the backend may still be working on that turn. */
  isLive: boolean;
  messages: ChatMessage[];
  isSending: boolean;
  activeTool: string;
  chatError: string;
  /** Verified aggregates from this turn's completed lookups, in call order. */
  resultFrames: ChatToolResultEvent[];
  events: CommandCenterState & { status: string; error: string; retry: () => void };
  agents: RegistryAgent[];
  agentsError: string;
  /** True while the Command Center workspace is on screen as the active view. */
  workspaceOwnsActiveView: boolean;
  /** Claim/release the inline active view so the flow renders exactly once. */
  setWorkspaceOwnsActiveView(claimed: boolean): void;
  /** Returns false when a turn is already in flight. */
  send(prompt: string): boolean;
  /** Re-submit the last request after a failure. */
  retry(): void;
  /** The stored conversation this panel is showing. */
  threadId: string;
  /** Reopen a stored conversation: its saved turns replace the panel. */
  openThread(threadId: string): void;
  /** Start a fresh conversation (the old one stays stored). */
  newChat(): void;
  /** Escape / close: hide the overlay. The request itself keeps running. */
  dismiss(): void;
}

const ExecutionContext = createContext<ExecutionController | null>(null);

function createId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// The in-flight turn is mirrored to session storage as soon as its task
// id is known. If the view dies mid-run - a page reload, a discarded
// background tab, a dev-server restart - the next provider mount
// re-attaches to the same task (its event stream is resumable) and
// waits for the stored answer, instead of stranding the question with
// no live view and no animation (the user's Oct 24 report).
const ACTIVE_TURN_STORAGE_KEY = "biztek.active-turn.v1";
const RECOVERY_POLL_MS = 3000;
const RECOVERY_TIMEOUT_MS = 180_000;

interface StoredActiveTurn {
  turnId: string;
  taskId: string;
  prompt: string;
  threadId: string;
  startedAt: number;
}

function readStoredActiveTurn(): StoredActiveTurn | null {
  try {
    const raw = window.sessionStorage.getItem(ACTIVE_TURN_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredActiveTurn;
    if (
      !parsed ||
      typeof parsed.turnId !== "string" ||
      typeof parsed.taskId !== "string" ||
      typeof parsed.prompt !== "string" ||
      typeof parsed.threadId !== "string"
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function clearStoredActiveTurn(): void {
  try {
    window.sessionStorage.removeItem(ACTIVE_TURN_STORAGE_KEY);
  } catch {
    // storage unavailable - recovery simply cannot persist
  }
}

export function ExecutionControllerProvider({ children }: { children: ReactNode }) {
  const [turn, setTurn] = useState<ActiveTurn | null>(null);
  const [lastTurn, setLastTurn] = useState<ActiveTurn | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [activeTool, setActiveTool] = useState("");
  const [chatError, setChatError] = useState("");
  // The Command Center tab renders the active operation view inline; while it
  // does, the full-screen overlay must not render a second copy of it.
  const [workspaceOwnsActiveView, setWorkspaceOwnsActiveView] = useState(false);

  // Facts the backend computed for this turn's lookups, carried on the chat
  // stream as they happen. Kept per turn so a finished answer cannot be
  // re-described by a later request's numbers.
  const [facts, setFacts] = useState<{ turnId: string; frames: ChatToolResultEvent[] }>({
    turnId: "",
    frames: [],
  });

  // Mirrors `turn` / `isSending` synchronously, so beginTurn can answer
  // "already running?" without depending on when React flushes updates.
  const turnRef = useRef<ActiveTurn | null>(null);
  const sendingRef = useRef(false);
  const lastPromptRef = useRef("");
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { agents, error: agentsError } = useAgents();

  // The stored conversation behind this panel. Its turns live on the
  // server (chat_threads), so reopening the app - or picking the chat on
  // the Chats tab - restores exactly what was said, and sending continues
  // the same thread instead of starting over.
  const [threadId, setThreadId] = useState("");
  const seededThreadRef = useRef("");
  const messagesRef = useRef<ChatMessage[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const loadStoredTurns = useCallback(async (id: string) => {
    try {
      const stored = await fetchChatThread(id);
      if (seededThreadRef.current !== id) return;
      if (turnRef.current !== null || sendingRef.current) return;
      if (messagesRef.current.length > 0) return;
      setMessages(
        (stored.turns ?? []).flatMap((storedTurn, index) => [
          { id: `${id}-q${index}`, role: "user" as const, content: storedTurn.question },
          { id: `${id}-a${index}`, role: "assistant" as const, content: storedTurn.answer },
        ]),
      );
    } catch {
      // A thread the server has never seen (a brand-new conversation)
      // simply starts empty.
    }
  }, []);

  useEffect(() => {
    let active = true;
    const id = getChatThreadId();
    seededThreadRef.current = id;
    void (async () => {
      await loadStoredTurns(id);
      if (active) setThreadId(id);
    })();
    return () => {
      active = false;
    };
  }, [loadStoredTurns]);

  const resetConversationView = useCallback(() => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    clearStoredActiveTurn();
    turnRef.current = null;
    setTurn(null);
    setLastTurn(null);
    setMessages([]);
    setFacts({ turnId: "", frames: [] });
    setChatError("");
    setActiveTool("");
  }, []);

  const openThread = useCallback(
    (id: string) => {
      if (sendingRef.current || !id) return;
      setChatThreadId(id);
      setThreadId(id);
      seededThreadRef.current = id;
      resetConversationView();
      void loadStoredTurns(id);
    },
    [loadStoredTurns, resetConversationView],
  );

  const newChat = useCallback(() => {
    if (sendingRef.current) return;
    const id = startNewChatThread();
    setThreadId(id);
    seededThreadRef.current = id;
    resetConversationView();
  }, [resetConversationView]);

  // One stream for the active or most recently completed turn. While a new
  // turn has no task id yet, this is intentionally null rather than the
  // previous task: stale events must never populate a newer request.
  const activeTaskId = turn ? turn.taskId : lastTurn?.taskId ?? null;
  const events = useTaskEvents(activeTaskId);

  const commit = useCallback((next: ActiveTurn | null) => {
    turnRef.current = next;
    setTurn(next);
  }, []);

  const clearCloseTimer = useCallback(() => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const beginTurn = useCallback(
    (prompt: string) => {
      const current = turnRef.current;
      // Only a finished or failed turn may be replaced; an in-flight one is
      // refused, so a request can never be submitted twice.
      if (current && current.phase !== "completed" && current.phase !== "failed") {
        return null;
      }
      clearCloseTimer();
      const next: ActiveTurn = {
        turnId: createId(),
        prompt,
        taskId: null,
        phase: "starting",
        message: "",
        startedAt: Date.now(),
      };
      commit(next);
      setLastTurn(next);
      return next.turnId;
    },
    [clearCloseTimer, commit],
  );

  const attachTask = useCallback(
    (turnId: string, taskId: string) => {
      const current = turnRef.current;
      if (!current || current.turnId !== turnId) return;
      // The first task id for a turn wins. A later frame cannot retarget events.
      if (current.taskId) return;
      const next = { ...current, taskId };
      commit(next);
      setLastTurn((previous) => (previous?.turnId === turnId ? next : previous));
    },
    [commit],
  );

  const updatePhase = useCallback(
    (turnId: string, phase: TurnPhase, message = "") => {
      const current = turnRef.current;
      if (!current || current.turnId !== turnId) return;
      const next = { ...current, phase, message };
      commit(next);
      setLastTurn((previous) => (previous?.turnId === turnId ? next : previous));
    },
    [commit],
  );

  const markSynthesizing = useCallback(
    (turnId: string) => updatePhase(turnId, "synthesizing"),
    [updatePhase],
  );
  const markWorking = useCallback(
    (turnId: string) => updatePhase(turnId, "starting"),
    [updatePhase],
  );

  const scheduleClose = useCallback(() => {
    clearCloseTimer();
    closeTimer.current = setTimeout(() => {
      closeTimer.current = null;
      const current = turnRef.current;
      if (current?.phase === "completed") {
        commit(null);
      }
    }, EXIT_TRANSITION_MS);
  }, [clearCloseTimer, commit]);

  const finishTurn = useCallback(
    (turnId: string) => {
      updatePhase(turnId, "completed");
      scheduleClose();
    },
    [scheduleClose, updatePhase],
  );

  const failTurn = useCallback(
    (turnId: string, message: string) => {
      // A failure stays visible with a recovery action; it never silently
      // disappears or pretends the request finished.
      updatePhase(turnId, "failed", message || "That request did not complete.");
    },
    [updatePhase],
  );

  // Mirror the in-flight turn to session storage (see the storage note
  // above): written while it runs, cleared the moment it settles.
  useEffect(() => {
    if (!turn || !threadId) return;
    if (turn.phase === "completed" || turn.phase === "failed" || !turn.taskId) {
      if (turn.phase === "completed" || turn.phase === "failed") {
        clearStoredActiveTurn();
      }
      return;
    }
    try {
      window.sessionStorage.setItem(
        ACTIVE_TURN_STORAGE_KEY,
        JSON.stringify({
          turnId: turn.turnId,
          taskId: turn.taskId,
          prompt: turn.prompt,
          threadId,
          startedAt: turn.startedAt,
        } satisfies StoredActiveTurn),
      );
    } catch {
      // storage unavailable - the live view still works this session
    }
  }, [turn, threadId]);

  // Re-attach to a turn that was in flight when the previous view died.
  const recoveryRef = useRef<{ turnId: string; assistantId: string }>({
    turnId: "",
    assistantId: "",
  });
  const recoveryAttemptedRef = useRef(false);
  const recoveryTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopRecoveryTimer = useCallback(() => {
    if (recoveryTimerRef.current) {
      clearInterval(recoveryTimerRef.current);
      recoveryTimerRef.current = null;
    }
  }, []);

  const endRecovery = useCallback(() => {
    stopRecoveryTimer();
    recoveryRef.current = { turnId: "", assistantId: "" };
    sendingRef.current = false;
    setIsSending(false);
    clearStoredActiveTurn();
  }, [stopRecoveryTimer]);

  // The answer itself travelled on the dead request's stream; the
  // server stores it on the thread when the turn completes, so recovery
  // polls the thread until it lands (the task's own events drive the
  // live view meanwhile, through the normal resumable stream).
  const startRecoveryPoll = useCallback(
    (stored: StoredActiveTurn, forThreadId: string) => {
      stopRecoveryTimer();
      const deadline = Date.now() + RECOVERY_TIMEOUT_MS;
      recoveryTimerRef.current = setInterval(() => {
        void (async () => {
          if (recoveryRef.current.turnId !== stored.turnId) return;
          if (Date.now() > deadline) {
            endRecovery();
            setChatError("The answer did not come back after the view was interrupted.");
            failTurn(stored.turnId, "The answer did not come back after the view was interrupted.");
            return;
          }
          try {
            const thread = await fetchChatThread(forThreadId);
            const turns = thread.turns ?? [];
            const lastStored = turns[turns.length - 1];
            if (lastStored && lastStored.question === stored.prompt && lastStored.answer) {
              const answer = lastStored.answer;
              // captured now: endRecovery clears the ref before React
              // runs this updater, and the updater must still find the
              // bubble it is filling
              const assistantId = recoveryRef.current.assistantId;
              setMessages((current) =>
                current.map((entry) =>
                  entry.id === assistantId ? { ...entry, content: answer } : entry,
                ),
              );
              endRecovery();
              finishTurn(stored.turnId);
            }
          } catch {
            // keep waiting; the deadline bounds the wait
          }
        })();
      }, RECOVERY_POLL_MS);
    },
    [endRecovery, failTurn, finishTurn, stopRecoveryTimer],
  );

  useEffect(() => {
    if (!threadId) return;
    const stored = readStoredActiveTurn();
    if (!stored || stored.threadId !== threadId) return;
    if (recoveryAttemptedRef.current) {
      // A strict-mode remount can clear the poll timer mid-recovery;
      // restart it for the recovery already in progress.
      if (
        recoveryRef.current.turnId === stored.turnId &&
        !recoveryTimerRef.current &&
        turnRef.current?.turnId === stored.turnId
      ) {
        startRecoveryPoll(stored, threadId);
      }
      return;
    }
    recoveryAttemptedRef.current = true;
    if (turnRef.current !== null || sendingRef.current) return;

    void (async () => {
      // If the finished turn already reached storage, the seeded
      // messages show it - there is nothing to recover.
      try {
        const thread = await fetchChatThread(threadId);
        const turns = thread.turns ?? [];
        const lastStored = turns[turns.length - 1];
        if (lastStored && lastStored.question === stored.prompt && lastStored.answer) {
          clearStoredActiveTurn();
          return;
        }
      } catch {
        // fall through and recover from the task itself
      }
      // A second mount racing this one loses here: the winner has
      // already committed the turn and marked the send in flight.
      if (turnRef.current !== null || sendingRef.current) return;

      const recovered: ActiveTurn = {
        turnId: stored.turnId,
        prompt: stored.prompt,
        taskId: stored.taskId,
        phase: "starting",
        message: "",
        startedAt: stored.startedAt,
      };
      const assistantId = createId();
      recoveryRef.current = { turnId: stored.turnId, assistantId };
      commit(recovered);
      setLastTurn(recovered);
      sendingRef.current = true;
      setIsSending(true);
      // The seeded turns may already end with this very question (the
      // server stored it before its answer existed): reuse that pair -
      // and its assistant bubble as the fill target - instead of
      // appending a duplicate.
      const existing = messagesRef.current;
      const lastUserIndex = existing.map((entry) => entry.role).lastIndexOf("user");
      const lastUser = lastUserIndex >= 0 ? existing[lastUserIndex] : undefined;
      if (lastUser && lastUser.content === stored.prompt) {
        const following = existing[lastUserIndex + 1];
        if (following && following.role === "assistant") {
          recoveryRef.current = { turnId: stored.turnId, assistantId: following.id };
        } else {
          setMessages([
            ...existing,
            { id: assistantId, role: "assistant" as const, content: "" },
          ]);
        }
      } else {
        setMessages([
          ...existing,
          { id: createId(), role: "user" as const, content: stored.prompt },
          { id: assistantId, role: "assistant" as const, content: "" },
        ]);
      }
      startRecoveryPoll(stored, threadId);
    })();
  }, [threadId, commit, startRecoveryPoll]);

  // A recovered task that the server marks failed ends the recovery at
  // once instead of polling until the deadline.
  useEffect(() => {
    const turnId = recoveryRef.current.turnId;
    if (!turnId) return;
    if (events.taskStatus === "failed" || events.taskStatus === "cancelled") {
      endRecovery();
      setChatError("That request did not complete.");
      failTurn(turnId, "That request did not complete.");
    }
  }, [events.taskStatus, endRecovery, failTurn]);

  // A pending recovery poll must not outlive the provider.
  useEffect(() => stopRecoveryTimer, [stopRecoveryTimer]);

  // Hiding the view only. There is no cancel endpoint anywhere in this system,
  // so dismissing must never imply backend work stopped.
  const dismiss = useCallback(() => {
    clearCloseTimer();
    commit(null);
  }, [clearCloseTimer, commit]);

  const send = useCallback(
    (prompt: string) => {
      const text = prompt.trim();
      if (!text || sendingRef.current) return false;

      const turnId = beginTurn(text);
      if (!turnId) return false;

      const userMessageId = createId();
      const assistantMessageId = createId();
      const append = (content: string) =>
        setMessages((current) => [
          ...current,
          { id: userMessageId, role: "user", content: text },
          { id: assistantMessageId, role: "assistant", content },
        ]);
      const setAssistant = (next: (content: string) => string) =>
        setMessages((current) =>
          current.map((entry) =>
            entry.id === assistantMessageId
              ? { ...entry, content: next(entry.content) }
              : entry,
          ),
        );

      lastPromptRef.current = text;
      sendingRef.current = true;
      setIsSending(true);
      setChatError("");
      setActiveTool("");
      setFacts({ turnId, frames: [] });
      append("");

      const handleStreamEvent = (streamEvent: ChatStreamEvent) => {
        switch (streamEvent.event) {
          case "task":
            attachTask(turnId, streamEvent.data.task_id);
            break;
          case "tool":
            // Real tool activity. The raw identifier is mapped to a human
            // phrase by the UI; it is never shown to the executive.
            setActiveTool(streamEvent.data.name);
            break;
          case "tool_result":
            // Aggregates the backend computed for that call. Frames without a
            // tool name are ignored rather than guessed at.
            setFacts((current) =>
              current.turnId === turnId
                ? { ...current, frames: [...current.frames, streamEvent.data] }
                : current,
            );
            break;
          case "token":
            markSynthesizing(turnId);
            setActiveTool("");
            setAssistant((content) => content + streamEvent.data.text);
            break;
          case "reset":
            // Provisional streamed text is discarded: the model chose a tool.
            markWorking(turnId);
            setActiveTool("");
            setAssistant(() => "");
            break;
          case "done":
            setActiveTool("");
            setAssistant((content) => streamEvent.data.text || content);
            finishTurn(turnId);
            break;
          case "error":
            setChatError(streamEvent.data.message);
            break;
        }
      };

      void (async () => {
        try {
          await sendMessage(text, handleStreamEvent);
        } catch (error) {
          console.error("Chat interaction failed.", error);
          const reason =
            error instanceof Error
              ? error.message
              : "Message could not be sent. Please try again.";
          setChatError(reason);
          failTurn(turnId, reason);
        } finally {
          sendingRef.current = false;
          setIsSending(false);
          setActiveTool("");
        }
      })();

      return true;
    },
    [attachTask, beginTurn, failTurn, finishTurn, markSynthesizing, markWorking],
  );

  const retry = useCallback(() => {
    if (sendingRef.current) return;
    const prompt = lastPromptRef.current;
    if (!prompt) return;
    dismiss();
    send(prompt);
  }, [dismiss, send]);

  // A pending close timer must not fire after the provider unmounts.
  useEffect(() => () => clearCloseTimer(), [clearCloseTimer]);

  // Only real task events can say work happened; the turn phase alone never
  // promotes the view past "starting".
  const hasAgentActivity = events.timeline.some((entry) => entry.type.startsWith("agent."));
  const hasToolActivity = events.timeline.some((entry) => entry.type.startsWith("tool."));

  const execution = useMemo<ExecutionPhase>(
    () =>
      deriveExecution({
        turn,
        taskStatus: events.taskStatus,
        hasAgentActivity,
        hasToolActivity,
      }),
    [turn, events.taskStatus, hasAgentActivity, hasToolActivity],
  );

  const live = isLivePhase(execution);

  // Only the visible turn's own frames. After a new turn begins, the previous
  // answer's numbers cannot follow it.
  const visibleTurnId = (turn ?? lastTurn)?.turnId ?? "";
  const resultFrames = useMemo(
    () => (facts.turnId === visibleTurnId ? facts.frames : []),
    [facts, visibleTurnId],
  );

  const value = useMemo<ExecutionController>(
    () => ({
      turn,
      lastTurn,
      execution,
      isLive: live,
      messages,
      isSending,
      activeTool,
      chatError,
      resultFrames,
      events,
      agents,
      agentsError,
      workspaceOwnsActiveView,
      setWorkspaceOwnsActiveView,
      dismiss,
      send,
      retry,
      threadId,
      openThread,
      newChat,
    }),
    [
      turn,
      lastTurn,
      live,
      execution,
      messages,
      isSending,
      activeTool,
      chatError,
      resultFrames,
      events,
      agents,
      agentsError,
      workspaceOwnsActiveView,
      dismiss,
      send,
      retry,
      threadId,
      openThread,
      newChat,
    ],
  );

  return <ExecutionContext.Provider value={value}>{children}</ExecutionContext.Provider>;
}

export function useExecutionController(): ExecutionController {
  const controller = useContext(ExecutionContext);
  if (!controller) {
    throw new Error("useExecutionController must be used inside ExecutionControllerProvider.");
  }
  return controller;
}
