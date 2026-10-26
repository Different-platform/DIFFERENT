// Server-side proxy for the chat SSE stream (Requirement 16.2, 16.3).
//
// SAFETY-CRITICAL (secrets exposure). This is the only place that knows the
// backend URL and the shared secret. The browser talks only to this route,
// on its own origin; it never sees API_BASE_URL, API_SHARED_SECRET, the Groq
// key, or the database URL. Those live in the server-side environment of this
// route handler and are never serialized into the client bundle.
//
// This is the specific reason the frontend must be Next.js (a server) and not
// the Vite SPA (no server): a browser-only app cannot hold a secret, so it
// would have to call the backend directly and expose it. See
// PROJECT_HANDOFF.md §12.1.

import { NextRequest } from "next/server";

// Never statically optimized; always runs on the server per request.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Server-only configuration. Note: NOT prefixed NEXT_PUBLIC_, so Next.js will
// refuse to inline these into client code — a build-time guarantee the secret
// cannot leak to the browser.
const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const API_SHARED_SECRET = process.env.API_SHARED_SECRET ?? "";

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid JSON body" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }

  const headers: Record<string, string> = { "content-type": "application/json" };
  if (API_SHARED_SECRET) {
    headers["x-api-secret"] = API_SHARED_SECRET;
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch (err) {
    return new Response(
      JSON.stringify({ error: `backend unreachable: ${String(err)}` }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text().catch(() => "");
    return new Response(
      JSON.stringify({ error: `backend error ${upstream.status}: ${text.slice(0, 500)}` }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  // Stream the SSE response straight through to the browser unchanged, so the
  // client's reader sees exactly the backend's event sequence.
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
