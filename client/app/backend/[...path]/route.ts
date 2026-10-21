// Same-origin proxy for the FastAPI backend, so the X-API-Key never reaches
// the browser. The client calls /backend/<path>; this handler runs on the
// server, adds the key from BACKEND_API_KEY and streams the answer back
// (including the /api/live and /api/chat SSE streams).
//
// Env (server-side only, see client/.env.example):
//   BACKEND_API_URL   default http://localhost:8000
//   BACKEND_API_KEY   the value of one API_KEYS entry on the backend

import type { NextRequest } from "next/server";

const UPSTREAM_BASE = (
  process.env.BACKEND_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

const API_KEY = process.env.BACKEND_API_KEY ?? "";

type ProxyContext = { params: Promise<{ path: string[] }> };

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ detail: message }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function proxy(request: NextRequest, context: ProxyContext): Promise<Response> {
  const { path } = await context.params;
  const target = `${UPSTREAM_BASE}/${path.join("/")}${request.nextUrl.search}`;

  const headers: Record<string, string> = {};
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const contentType = request.headers.get("content-type");
  if (contentType) headers["Content-Type"] = contentType;

  const init: RequestInit = { method: request.method, headers, cache: "no-store" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    // a streamed request body needs duplex in the undici fetch implementation
    Object.assign(init, { body: request.body, duplex: "half" });
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return jsonError(`Cannot reach the backend at ${UPSTREAM_BASE}.`, 502);
  }

  const responseHeaders = new Headers();
  const upstreamType = upstream.headers.get("content-type");
  if (upstreamType) responseHeaders.set("Content-Type", upstreamType);
  // SSE must not be buffered by anything between FastAPI and the browser
  responseHeaders.set("Cache-Control", "no-cache");
  responseHeaders.set("X-Accel-Buffering", "no");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest, context: ProxyContext) {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: ProxyContext) {
  return proxy(request, context);
}
