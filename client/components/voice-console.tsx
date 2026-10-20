import { Mic, Radio } from "lucide-react";
import { voiceConsoleData } from "@/lib/mockData";

export function VoiceConsole() {
  return (
    <section aria-labelledby="voice-title" className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-start justify-between">
        <div>
          <h2 id="voice-title" className="font-semibold">Voice console</h2>
          <p className="mt-1 text-xs text-muted">Push-to-talk interface</p>
        </div>
        <span className="inline-flex items-center gap-1.5 text-xs text-muted">
          <Radio size={14} aria-hidden="true" className="text-accent" />
          {voiceConsoleData.status}
        </span>
      </div>
      <div className="mt-6 flex flex-col items-center rounded-lg border border-dashed border-border bg-[#0c0f15] px-4 py-7">
        <button
          type="button"
          aria-label={voiceConsoleData.instruction}
          className="grid h-16 w-16 place-items-center rounded-full bg-accent text-[#11150b] transition-transform active:scale-95"
        >
          <Mic size={25} aria-hidden="true" />
        </button>
        <p className="mt-4 text-sm font-medium">{voiceConsoleData.instruction}</p>
        <p className="mt-1 text-xs text-muted">Microphone interaction is a presentation placeholder</p>
      </div>
      <div className="mt-4 border-t border-border pt-4">
        <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Latest transcript</p>
        <p className="mt-2 text-sm text-muted">{voiceConsoleData.lastTranscript}</p>
      </div>
    </section>
  );
}
