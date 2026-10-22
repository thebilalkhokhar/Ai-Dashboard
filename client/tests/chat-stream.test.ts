import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sendMessage } from "@/services/api";

// A Response body that emits the given SSE frames, like the FastAPI /api/chat
// endpoint does. No network is involved; fetch itself is replaced.
function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("chat SSE contract", () => {
  it("surfaces the announced task id and forwards the turn's events in order", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        'event: task\ndata: {"task_id":"task-9","thread_id":"t-1"}\n\n',
        'event: tool\ndata: {"name":"get_invoices"}\n\n',
        'event: token\ndata: {"text":"Hello "}\n\n',
        'event: token\ndata: {"text":"there"}\n\n',
        'event: done\ndata: {"text":"Hello there","thread_id":"t-1","task_id":"task-9"}\n\n',
      ]),
    );

    const seen: string[] = [];
    let taskId = "";
    const done = await sendMessage("hi", (event) => {
      seen.push(event.event);
      if (event.event === "task") taskId = event.data.task_id;
    });

    expect(seen).toEqual(["task", "tool", "token", "token", "done"]);
    expect(taskId).toBe("task-9");
    expect(done.task_id).toBe("task-9");
    expect(done.text).toBe("Hello there");
  });

  it("still works against a backend that has no task frame", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        'event: tool\ndata: {"name":"get_invoices"}\n\n',
        'event: done\ndata: {"text":"Answer","thread_id":"t-1"}\n\n',
      ]),
    );

    const seen: string[] = [];
    const done = await sendMessage("hi", (event) => seen.push(event.event));
    expect(seen).toEqual(["tool", "done"]);
    expect(done.task_id).toBeUndefined();
  });

  it("ignores unrelated frames such as heartbeats", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        ": heartbeat\n\n",
        'event: task\ndata: {"task_id":"t9","thread_id":"t-1"}\n\n',
        'event: done\ndata: {"text":"A","thread_id":"t-1","task_id":"t9"}\n\n',
      ]),
    );
    const seen: string[] = [];
    await sendMessage("hi", (event) => seen.push(event.event));
    expect(seen).toEqual(["task", "done"]);
  });

  it("rejects when the stream reports an error", async () => {
    fetchMock.mockResolvedValue(
      sseResponse(['event: error\ndata: {"message":"Chat limit reached."}\n\n']),
    );
    await expect(sendMessage("hi", () => undefined)).rejects.toThrow(/Chat limit reached/);
  });

  it("carries verified result facts through a tool_result frame", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        'event: tool_result\ndata: {"tool":"get_payments","records_total":67,' +
          '"records_returned":10,"records_remaining":57,"complete":false,' +
          '"money_label":"sum_amount","money_total":290455.2,' +
          '"money_covers":"every matched payment record, not only the rows on this page",' +
          '"window":{"date_from":"2026-10-19","date_to":"2026-10-22"}}\n\n',
        'event: done\ndata: {"text":"A","thread_id":"t-1"}\n\n',
      ]),
    );

    const frames: unknown[] = [];
    await sendMessage("hi", (event) => {
      if (event.event === "tool_result") frames.push(event.data);
    });

    expect(frames).toEqual([
      {
        tool: "get_payments",
        records_total: 67,
        records_returned: 10,
        records_remaining: 57,
        complete: false,
        money_label: "sum_amount",
        money_total: 290455.2,
        money_covers: "every matched payment record, not only the rows on this page",
        window: { date_from: "2026-10-19", date_to: "2026-10-22" },
      },
    ]);
  });

  it("drops fields of the wrong type instead of trusting the frame", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        'event: tool_result\ndata: {"tool":"get_leads","records_total":"67",' +
          '"complete":true,"window":{"date_from":2026}}\n\n',
        'event: done\ndata: {"text":"A","thread_id":"t-1"}\n\n',
      ]),
    );

    let data: unknown;
    await sendMessage("hi", (event) => {
      if (event.event === "tool_result") data = event.data;
    });

    expect(data).toEqual({
      tool: "get_leads",
      complete: true,
      window: { date_from: null, date_to: null },
    });
  });
});
