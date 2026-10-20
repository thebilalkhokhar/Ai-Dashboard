"use client";

import { Mic, Radio } from "lucide-react";

const MISSING_CONTRACT = [
  "POST /api/voice accepting an audio blob (multipart or raw body)",
  "An STT pipeline for Urdu/English code-switching",
  "A JSON or audio response the client can render",
];

export function VoiceConsole() {
  return (
    <section aria-labelledby="voice-title" className="flex min-h-full flex-col">
      <div className="flex items-start justify-between">
        <div>
          <h2 id="voice-title" className="font-semibold">Voice console</h2>
          <p className="mt-1 text-xs text-muted">Push-to-talk interface</p>
        </div>
        <span className="inline-flex items-center gap-1.5 text-xs text-amber-300">
          <Radio size={14} aria-hidden="true" />
          Backend endpoint missing
        </span>
      </div>

      <div className="mt-6 flex flex-1 flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-surface-raised/40 px-4 py-7 text-center">
        <button
          type="button"
          disabled
          aria-label="Voice console unavailable"
          className="grid h-16 w-16 cursor-not-allowed place-items-center rounded-full bg-accent text-[#11150b] opacity-40"
        >
          <Mic size={25} aria-hidden="true" />
        </button>
        <p className="mt-4 text-sm font-medium">Voice is not connected</p>
        <p className="mt-2 max-w-sm text-xs leading-5 text-muted">
          Recording audio here would only produce a file nobody can transcribe: the FastAPI
          backend does not implement <code className="text-foreground">POST /api/voice</code>{" "}
          (it returns 404). Text chat is fully connected.
        </p>
      </div>

      <div className="mt-4 border-t border-border pt-4">
        <p className="text-[11px] font-medium uppercase tracking-wider text-muted">
          Backend contract needed
        </p>
        <ul className="mt-2 space-y-1.5">
          {MISSING_CONTRACT.map((item) => (
            <li key={item} className="text-xs leading-5 text-muted">
              • {item}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
