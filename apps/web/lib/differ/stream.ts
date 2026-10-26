// Real SSE client — the drop-in replacement for the template's mockAsk().
//
// The Vite template's UI consumed a `StreamEvent` async generator and never
// cared where the events came from. This produces the identical event shape
// from the live backend, so the components bind to it unchanged. The one
// addition beyond the mock is the `error` event, which the backend emits on a
// pipeline failure and the UI must surface rather than hang.
//
// This talks to the SAME-ORIGIN `/api/chat` route (the Next.js proxy), never
// to the backend directly — that is what keeps the backend URL and secrets
// out of the browser (Requirement 16.2/16.3).

import type { Citation, NumericResult, RouteIntent, SqlResult, Verdict } from "./types";

export type StreamEvent =
  | { type: "route"; route: RouteIntent }
  | { type: "verdict"; verdict: Verdict }
  | { type: "citations"; citations: Citation[] }
  | { type: "sql"; sql: SqlResult }
  | { type: "result"; result: NumericResult }
  | { type: "token"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; message_id?: number | null };

export interface AskParams {
  question: string;
  year?: number | null;
  conversationId?: string | null;
  signal?: AbortSignal;
}

/**
 * POST a question to the proxy and yield the backend's SSE events in order:
 *   route -> verdict -> citations? -> sql? -> result? -> token* -> done
 *
 * Parses `data: {...}` lines from the text/event-stream response. Each data
 * line is one JSON StreamEvent, matching services/api/events.py.
 */
export async function* ask(params: AskParams): AsyncGenerator<StreamEvent, void, unknown> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      question: params.question,
      year: params.year ?? null,
      conversation_id: params.conversationId ?? null,
    }),
    signal: params.signal,
  });

  if (!response.ok || !response.body) {
    let detail = `request failed (${response.status})`;
    try {
      const data = await response.json();
      if (data?.error) detail = String(data.error);
    } catch {
      /* non-JSON error body; keep the status-based message */
    }
    yield { type: "error", message: detail };
    yield { type: "done", message_id: null };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line; each frame has `data:` lines.
      let sep: number;
      while ((sep = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, sep).trimEnd();
        buffer = buffer.slice(sep + 1);
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          yield JSON.parse(payload) as StreamEvent;
        } catch {
          // A partial/split JSON line; skip. Backend sends one event per
          // data line, so this should not happen, but never throw on input.
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
