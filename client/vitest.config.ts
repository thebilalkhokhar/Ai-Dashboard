import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Client unit tests run in jsdom; `next build` stays the real browser check.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["./tests/setup.ts"],
  },
});
