import { beforeEach, describe, expect, it, vi } from "vitest";

// The current conversation must survive a browser restart: its id lives
// in localStorage now (it used to be sessionStorage, which dies with the
// tab session), and the stored turns on the server do the rest.

describe("chat thread storage", () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("reuses the stored thread id across a fresh page load", async () => {
    window.localStorage.setItem("biztek-chat-thread-id", "thread-kept");
    const api = await import("@/services/api");
    expect(api.getChatThreadId()).toBe("thread-kept");
  });

  it("migrates a legacy session-stored id instead of orphaning the chat", async () => {
    window.sessionStorage.setItem("biztek-chat-thread-id", "thread-legacy");
    const api = await import("@/services/api");
    expect(api.getChatThreadId()).toBe("thread-legacy");
    expect(window.localStorage.getItem("biztek-chat-thread-id")).toBe("thread-legacy");
  });

  it("starts a new chat by switching the stored id", async () => {
    window.localStorage.setItem("biztek-chat-thread-id", "thread-old");
    const api = await import("@/services/api");
    const fresh = api.startNewChatThread();
    expect(fresh).not.toBe("thread-old");
    expect(api.getChatThreadId()).toBe(fresh);
    expect(window.localStorage.getItem("biztek-chat-thread-id")).toBe(fresh);
  });
});
