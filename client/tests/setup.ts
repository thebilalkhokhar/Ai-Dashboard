import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// Vitest runs without globals, so Testing Library's automatic cleanup never
// registers itself; unmounting between tests is what keeps the DOM clean.
afterEach(cleanup);
