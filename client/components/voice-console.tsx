"use client";

import { useEffect, useRef, useState } from "react";
import { Mic, Radio } from "lucide-react";
import { voiceConsoleData } from "@/lib/mockData";
import { sendVoiceAction } from "@/services/api";

function getRecordingMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"];
  return candidates.find((mimeType) => MediaRecorder.isTypeSupported(mimeType)) ?? "";
}

export function VoiceConsole() {
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [transcript, setTranscript] = useState(voiceConsoleData.lastTranscript);
  const [errorMessage, setErrorMessage] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const isHoldingRef = useRef(false);

  useEffect(() => () => {
    isHoldingRef.current = false;
    if (recorderRef.current) {
      recorderRef.current.ondataavailable = null;
      recorderRef.current.onerror = null;
      recorderRef.current.onstop = null;
      if (recorderRef.current.state === "recording") recorderRef.current.stop();
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  async function startRecording() {
    if (isProcessing || isHoldingRef.current) return;
    isHoldingRef.current = true;
    setErrorMessage("");

    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("Microphone recording is not supported by this browser.");
      }
      if (typeof MediaRecorder === "undefined") {
        throw new Error("Audio recording is not supported by this browser.");
      }

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!isHoldingRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      streamRef.current = stream;
      const mimeType = getRecordingMimeType();
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      const chunks: BlobPart[] = [];
      let recordingFailed = false;
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      recorder.onerror = () => {
        recordingFailed = true;
        setErrorMessage("Recording failed. Please check your microphone and try again.");
        if (recorder.state !== "inactive") recorder.stop();
      };
      recorder.onstop = async () => {
        const audioBlob = new Blob(chunks, {
          type: recorder.mimeType || mimeType || "audio/webm",
        });
        stream.getTracks().forEach((track) => track.stop());
        if (streamRef.current === stream) streamRef.current = null;
        if (recorderRef.current === recorder) recorderRef.current = null;
        setIsRecording(false);

        if (recordingFailed) return;
        if (audioBlob.size === 0) {
          setErrorMessage("No audio was recorded. Hold the microphone button while speaking.");
          return;
        }

        setIsProcessing(true);
        try {
          const response = await sendVoiceAction(audioBlob);
          setTranscript(response.lastTranscript);
        } catch (error) {
          console.error("Voice interaction failed.", error);
          setErrorMessage(
            error instanceof Error
              ? error.message
              : "Voice request could not be processed. Please try again.",
          );
        } finally {
          setIsProcessing(false);
        }
      };

      recorder.start();
      setIsRecording(true);
    } catch (error) {
      isHoldingRef.current = false;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      recorderRef.current = null;
      console.error("Unable to start microphone recording.", error);
      if (error instanceof Error && error.name === "NotAllowedError") {
        setErrorMessage("Microphone permission was denied. Allow microphone access and try again.");
      } else if (error instanceof Error && error.name === "NotFoundError") {
        setErrorMessage("No microphone was found. Connect a microphone and try again.");
      } else {
        setErrorMessage(
          error instanceof Error ? error.message : "Could not access the microphone.",
        );
      }
    }
  }

  function stopRecording() {
    isHoldingRef.current = false;
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") recorder.stop();
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
          {isRecording ? "Recording..." : isProcessing ? "Processing..." : voiceConsoleData.status}
        </span>
      </div>
      <div className="mt-6 flex flex-1 flex-col items-center justify-center rounded-2xl bg-surface-raised/40 px-4 py-7">
        <button
          type="button"
          onPointerDown={(event) => {
            event.preventDefault();
            void startRecording();
          }}
          onPointerUp={stopRecording}
          onPointerLeave={stopRecording}
          onPointerCancel={stopRecording}
          onKeyDown={(event) => {
            if ((event.key === " " || event.key === "Enter") && !event.repeat) {
              event.preventDefault();
              void startRecording();
            }
          }}
          onKeyUp={(event) => {
            if (event.key === " " || event.key === "Enter") stopRecording();
          }}
          aria-label={
            isRecording
              ? "Recording voice input, release to send"
              : isProcessing
                ? "Processing voice input"
                : voiceConsoleData.instruction
          }
          aria-pressed={isRecording}
          disabled={isProcessing}
          className={`grid h-16 w-16 place-items-center rounded-full text-[#11150b] transition-transform active:scale-95 disabled:cursor-wait disabled:opacity-60 ${
            isRecording
              ? "animate-pulse bg-red-400 ring-4 ring-red-400/20"
              : "bg-accent"
          }`}
        >
          <Mic size={25} aria-hidden="true" />
        </button>
        <p className="mt-4 text-sm font-medium" role="status">
          {isRecording ? "Recording... release to send" : isProcessing ? "Processing..." : voiceConsoleData.instruction}
        </p>
        <p className="mt-1 text-xs text-muted">Audio is recorded only while the button is held</p>
      </div>
      {errorMessage && <p className="mt-3 text-sm text-red-400" role="alert">{errorMessage}</p>}
      <div className="mt-4 border-t border-border pt-4">
        <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Latest transcript</p>
        <p className="mt-2 text-sm text-muted">{transcript}</p>
      </div>
    </section>
  );
}
