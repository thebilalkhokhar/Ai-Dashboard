"use client";

// Execution history, intentionally separate from the live workspace.
//
// The drawer lists persisted tasks from GET /api/tasks, using the prompt the
// backend recorded for chat tasks (or the real trigger for pipeline tasks).
// Selecting one replays its persisted events through the existing
// GET /api/tasks/{id}/events endpoint. It is labelled as recorded history and
// never animates the live orb or pretends the work is executing now.

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, History, Loader2, RotateCcw } from "lucide-react";
import { fetchTasks } from "@/services/api";
import { useTaskEvents } from "@/hooks/use-task-events";
import { ActivityTimeline } from "@/components/command-center/activity-timeline";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { formatDateTime, formatDuration } from "@/lib/format";
import { taskTitle } from "@/lib/presentation";
import type { TaskListStatus, TaskSummary } from "@/types/api";

const STATUS_WORD: Record<TaskListStatus, string> = {
  created: "Queued",
  running: "In progress",
  completed: "Done",
  failed: "Did not finish",
  cancelled: "Stopped",
  // the backend recorded no ending and no recent activity: say so
  unknown: "Outcome not recorded",
};

// The exact set of `trigger` values the backend writes (services/scheduler.py
// and agents/graph.py). Anything outside it is shown as itself rather than
// quietly relabelled, so a new trigger cannot be disguised as a known one.
const TRIGGER_PHRASES: Record<string, string> = {
  daily_run: "Daily business review",
  manual: "Requested review",
  new_quote: "New quote arrived",
  new_overdue_invoice: "New overdue invoice",
  new_lead: "New lead arrived",
  erp_refresh: "Business data refresh",
  scheduler: "Scheduled assistant review",
};

function triggerLabel(trigger: string | null | undefined): string | null {
  if (!trigger) return null;
  const parts = trigger.trim().toLowerCase().split("_and_").filter(Boolean);
  if (parts.length === 0) return null;
  const phrases = parts.map((part) => TRIGGER_PHRASES[part] ?? part.replace(/_/g, " "));
  return phrases.join(" + ");
}

function fallbackTitle(task: TaskSummary): string {
  if (task.last_type.startsWith("erp.sync")) return "Business data refresh";
  if (task.last_type.startsWith("tool.")) return "Assistant lookup";
  if (task.last_type.startsWith("agent.")) return "Assistant review";
  return "Assistant request";
}

// Everything a row may say besides its title, built only from stored fields.
// An absent value is simply not shown - never replaced with a guess.
function rowFacts(task: TaskSummary): string[] {
  const facts: string[] = [];
  const duration = formatDuration(task.duration_ms);
  if (duration) facts.push(`took ${duration}`);
  const agents = (task.agents ?? []).filter(Boolean);
  if (agents.length === 1) facts.push(`${agents[0]} agent`);
  else if (agents.length > 1) facts.push(`${agents.length} agents`);
  if (task.error_kind) facts.push(`stopped: ${task.error_kind}`);
  return facts;
}

export function HistoryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="cc-history-button"
      aria-label="Open execution history"
    >
      <History size={14} aria-hidden="true" />
      History
    </button>
  );
}

export function HistoryDrawer({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<TaskSummary | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const isLoading = open && !loaded && !error;

  useEffect(() => {
    if (!open) return;
    let active = true;
    void fetchTasks(30)
      .then((response) => {
        if (!active) return;
        setTasks(response.tasks);
        setLoaded(true);
        setSelected((current) =>
          current && response.tasks.some((task) => task.task_id === current.task_id)
            ? response.tasks.find((task) => task.task_id === current.task_id) ?? null
            : null,
        );
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setError(cause instanceof Error ? cause.message : "Could not load execution history.");
        setLoaded(true);
      });
    return () => {
      active = false;
    };
  }, [open, reloadKey]);

  const retry = useCallback(() => {
    setLoaded(false);
    setError("");
    setReloadKey((value) => value + 1);
  }, []);

  function handleOpenChange(next: boolean) {
    if (next) {
      // Fresh loads start from an explicit loading state without writing
      // state synchronously inside the data-fetching effect.
      setTasks([]);
      setSelected(null);
      setError("");
      setLoaded(false);
    }
    onOpenChange(next);
  }

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent side="right" className="history-drawer gap-4 p-0">
        <SheetHeader className="border-b border-border px-5 py-4">
          <div className="flex items-center justify-between gap-3">
            <SheetTitle className="flex items-center gap-2 text-base">
              <History size={16} aria-hidden="true" />
              Execution history
            </SheetTitle>
            <button
              type="button"
              onClick={retry}
              aria-label="Reload execution history"
              className="rounded-md p-1.5 text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
            >
              <RotateCcw size={14} aria-hidden="true" />
            </button>
          </div>
          <p className="text-xs text-muted">
            Past requests and the steps the backend actually recorded.
          </p>
        </SheetHeader>

        {error ? (
          <div className="px-5" role="alert">
            <p className="flex items-start gap-2 rounded-lg border border-red-400/30 bg-red-400/5 px-3 py-2 text-xs text-red-200">
              <AlertCircle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
              {error}
            </p>
            <button
              type="button"
              onClick={retry}
              className="mt-3 text-xs text-muted underline-offset-4 hover:text-foreground hover:underline"
            >
              Try loading history again
            </button>
          </div>
        ) : isLoading && tasks.length === 0 ? (
          <p className="flex items-center gap-2 px-5 py-6 text-sm text-muted" role="status">
            <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            Loading execution history...
          </p>
        ) : tasks.length === 0 ? (
          <p className="px-5 py-6 text-sm text-muted" role="status">
            No recorded executions yet. Ask JARVIS a question to create one.
          </p>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <ul aria-label="Recorded executions" className="history-list">
              {tasks.map((task) => {
                const title =
                  taskTitle(task.prompt) !== "Assistant request"
                    ? taskTitle(task.prompt)
                    : triggerLabel(task.trigger) ?? fallbackTitle(task);
                const isSelected = selected?.task_id === task.task_id;
                return (
                  <li key={task.task_id}>
                    <button
                      type="button"
                      aria-pressed={isSelected}
                      onClick={() => setSelected(task)}
                      className="history-row"
                      data-active={isSelected ? "true" : "false"}
                    >
                      <span className="history-row-title">{title}</span>
                      <span className="history-row-meta">
                        <span className="history-status" data-status={task.status}>
                          {STATUS_WORD[task.status]}
                        </span>
                        <span>{formatDateTime(task.ts)}</span>
                        {rowFacts(task).map((fact) => (
                          <span key={fact} className="history-row-fact">
                            {fact}
                          </span>
                        ))}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>

            {selected && (
              <HistoryReplay
                key={selected.task_id}
                task={selected}
                onClose={() => setSelected(null)}
              />
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function HistoryReplay({ task, onClose }: { task: TaskSummary; onClose: () => void }) {
  const events = useTaskEvents(task.task_id);
  const pending = events.status === "connecting" && events.timeline.length === 0;
  const streamError = events.status === "error" || events.status === "offline";

  return (
    <section aria-label="Recorded task history" className="history-replay">
      <div className="history-replay-head">
        <div>
          <p className="history-replay-eyebrow">Recorded history</p>
          <p className="history-replay-title">
            {taskTitle(task.prompt) !== "Assistant request"
              ? taskTitle(task.prompt)
              : triggerLabel(task.trigger) ?? fallbackTitle(task)}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-[11px] text-muted underline-offset-4 hover:text-foreground hover:underline"
        >
          Close
        </button>
      </div>
      <p className="history-replay-note">
        {STATUS_WORD[task.status]} · Replaying persisted events. This is not a live execution.
      </p>
      <dl className="history-replay-facts">
        <div>
          <dt>Started</dt>
          <dd>{formatDateTime(task.created_at)}</dd>
        </div>
        <div>
          <dt>Last event</dt>
          <dd>{formatDateTime(task.updated_at ?? task.ts)}</dd>
        </div>
        <div>
          <dt>Duration</dt>
          {/* no terminal event, no duration - the dash is the honest answer */}
          <dd>{formatDuration(task.duration_ms) ?? "not recorded"}</dd>
        </div>
        <div>
          <dt>Agents</dt>
          <dd>{(task.agents ?? []).filter(Boolean).join(", ") || "none recorded"}</dd>
        </div>
        {task.error_kind && (
          <div>
            <dt>Stopped by</dt>
            <dd>{task.error_kind}</dd>
          </div>
        )}
      </dl>

      {streamError ? (
        <div className="history-replay-error" role="alert">
          <p>{events.error || "The recorded events could not be loaded."}</p>
          <button type="button" onClick={events.retry}>
            Retry replay
          </button>
        </div>
      ) : (
        <ActivityTimeline
          entries={events.timeline}
          details={events.details}
          showDiagnostics
          emptyMessage={pending ? "Loading recorded events..." : "No events were recorded for this task."}
        />
      )}
    </section>
  );
}
