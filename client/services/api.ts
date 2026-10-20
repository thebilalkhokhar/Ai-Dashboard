import {
  chatData,
  mockSignalsReport,
  voiceConsoleData,
} from "@/lib/mockData";
import type { SignalsReport } from "@/types/api";

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

export async function fetchSignals(): Promise<SignalsReport> {
  try {
    const response = await fetch("http://localhost:8000/api/signals", {
      cache: "no-store",
    });

    if (!response.ok) {
      throw new Error(`Signals request failed with status ${response.status}`);
    }

    return (await response.json()) as SignalsReport;
  } catch (error) {
    console.warn("Unable to load live signals; using mock signals report.", error);
    return mockSignalsReport;
  }
}

export async function sendMessage(message: string): Promise<typeof chatData> {
  try {
    const response = await fetch("http://localhost:8000/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });

    if (!response.ok) {
      throw new Error(`Chat request failed with status ${response.status}`);
    }

    return (await response.json()) as typeof chatData;
  } catch (error) {
    console.warn("Unable to send chat message; using mock response.", error);
    await wait(1500);
    return chatData;
  }
}

export async function submitApproval(
  threadId: string,
  approved: boolean,
): Promise<{ status: "success" }> {
  try {
    const response = await fetch("http://localhost:8000/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, approved }),
    });

    if (!response.ok) {
      throw new Error(`Approval request failed with status ${response.status}`);
    }

    return { status: "success" };
  } catch (error) {
    console.warn("Unable to submit approval; simulating successful response.", error);
    await wait(500);
    return { status: "success" };
  }
}

export async function sendVoiceAction(audioBlob: Blob): Promise<typeof voiceConsoleData> {
  try {
    const formData = new FormData();
    formData.append("audio", audioBlob, "voice-input.webm");

    const response = await fetch("http://localhost:8000/api/voice", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`Voice request failed with status ${response.status}`);
    }

    return (await response.json()) as typeof voiceConsoleData;
  } catch (error) {
    console.warn("Unable to submit voice input; using mock response.", error);
    await wait(2000);
    return voiceConsoleData;
  }
}
