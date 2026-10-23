import { beforeEach, describe, expect, it, vi } from "vitest";

// The chat panel shows the CURRENT session's conversation only: the
// thread id lives in sessionStorage, so a refresh inside the session
// keeps the conversation, a new visit starts fresh, and older
// conversations stay in the Chats archive instead of reopening
// themselves in the panel.

describe("chat thread storage", () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("keeps the conversation across a refresh inside the same session", async () => {
    window.sessionStorage.setItem("biztek-chat-thread-id", "thread-session");
    const api = await import("@/services/api");
    expect(api.getChatThreadId()).toBe("thread-session");
  });

  it("starts fresh on a new visit instead of reopening the previous session's chat", async () => {
    const api = await import("@/services/api");
    const fresh = api.getChatThreadId();
    expect(fresh).toBeTruthy();
    expect(window.sessionStorage.getItem("biztek-chat-thread-id")).toBe(fresh);
    expect(window.localStorage.getItem("biztek-chat-thread-id")).toBeNull();
  });

  it("ignores and clears a legacy localStorage id from the previous build", async () => {
    window.localStorage.setItem("biztek-chat-thread-id", "thread-legacy");
    const api = await import("@/services/api");
    const fresh = api.getChatThreadId();
    expect(fresh).not.toBe("thread-legacy");
    expect(window.localStorage.getItem("biztek-chat-thread-id")).toBeNull();
    expect(window.sessionStorage.getItem("biztek-chat-thread-id")).toBe(fresh);
  });

  it("starts a new chat by switching the session's stored id", async () => {
    window.sessionStorage.setItem("biztek-chat-thread-id", "thread-old");
    const api = await import("@/services/api");
    const fresh = api.startNewChatThread();
    expect(fresh).not.toBe("thread-old");
    expect(api.getChatThreadId()).toBe(fresh);
    expect(window.sessionStorage.getItem("biztek-chat-thread-id")).toBe(fresh);
  });

  it("opening an archived chat makes it this session's conversation", async () => {
    const api = await import("@/services/api");
    api.setChatThreadId("thread-from-archive");
    expect(api.getChatThreadId()).toBe("thread-from-archive");
    expect(window.sessionStorage.getItem("biztek-chat-thread-id")).toBe(
      "thread-from-archive",
    );
  });
});
