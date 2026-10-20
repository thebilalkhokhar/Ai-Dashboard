"use client";

import { useState } from "react";
import { Mic, Radio } from "lucide-react";
import { voiceConsoleData } from "@/lib/mockData";
import { sendVoiceAction } from "@/services/api";

export function VoiceConsole() {
  const [isProcessing, setIsProcessing] = useState(false);
  const [transcript, setTranscript] = useState(voiceConsoleData.lastTranscript);
  const [errorMessage, setErrorMessage] = useState("");

  async function handlePushToTalk() {
    if (isProcessing) return;

    setIsProcessing(true);
    setErrorMessage("");

    try {
      const response = await sendVoiceAction(new Blob([], { type: "audio/webm" }));
      setTranscript(response.lastTranscript);
    } catch (error) {
      console.error("Voice interaction failed.", error);
      setErrorMessage("Voice request could not be processed. Please try again.");
    } finally {
      setIsProcessing(false);
    }
  }

  return (
    <section aria-labelledby="voice-title" className="flex min-h-full flex-col">
      <div className="flex items-start justify-between">
        <div>
          <h2 id="voice-title" className="font-semibold">Voice console</h2>
          <p className="mt-1 text-xs text-muted">Push-to-talk interface</p>
        </div>
        <span className="inline-flex items-center gap-1.5 text-xs text-muted">
          <Radio size={14} aria-hidden="true" className="text-accent" />
          {isProcessing ? "Processing..." : voiceConsoleData.status}
        </span>
      </div>
      <div className="mt-6 flex flex-1 flex-col items-center justify-center rounded-2xl bg-surface-raised/40 px-4 py-7">
        <button
          type="button"
          onClick={() => void handlePushToTalk()}
          aria-label={isProcessing ? "Processing voice input" : voiceConsoleData.instruction}
          disabled={isProcessing}
          className="grid h-16 w-16 place-items-center rounded-full bg-accent text-[#11150b] transition-transform active:scale-95 disabled:cursor-wait disabled:opacity-60"
        >
          <Mic size={25} aria-hidden="true" />
        </button>
        <p className="mt-4 text-sm font-medium" role="status">
          {isProcessing ? "Processing..." : voiceConsoleData.instruction}
        </p>
        <p className="mt-1 text-xs text-muted">Microphone interaction is a presentation placeholder</p>
      </div>
      {errorMessage && <p className="mt-3 text-sm text-red-400" role="alert">{errorMessage}</p>}
      <div className="mt-4 border-t border-border pt-4">
        <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Latest transcript</p>
        <p className="mt-2 text-sm text-muted">{transcript}</p>
      </div>
    </section>
  );
}
