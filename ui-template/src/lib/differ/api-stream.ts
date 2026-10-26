/**
 * Real SSE reader against the DIFFER API.
 *
 * Exposes the same shape as `mockAsk` so swapping between them touches one
 * call site and no components. That was the whole point of isolating the data
 * layer: field names here are the API's snake_case contract verbatim.
 *
 * EventSource is not usable — it only issues GET requests, and /api/chat is a
 * POST carrying the question — so this reads the response body stream and
 * parses SSE frames directly.
 */

import type { StreamEvent } from "./mock-stream";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface AskOptions {
  conversationId?: string | null;
  year?: number | null;
  signal?: AbortSignal;
}

/** SSE frames are separated by a blank line; each `data:` line carries JSON. */
function parseFrames(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = [];
  const parts = buffer.split("\n\n");
  // The trailing element may be an incomplete frame; keep it buffered.
  const rest = parts.pop() ?? "";

  for (const frame of parts) {
    for (const line of frame.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      try {
        events.push(JSON.parse(payload) as StreamEvent);
      } catch {
        // A malformed frame should not kill the stream; skip it.
      }
    }
  }

  return { events, rest };
}

export async function* apiAsk(
  question: string,
  options: AskOptions = {},
): AsyncGenerator<StreamEvent, void, unknown> {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      conversation_id: options.conversationId ?? null,
      year: options.year ?? null,
    }),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    yield {
      type: "error",
      message: `The API returned ${response.status} ${response.statusText}.`,
    };
    yield { type: "done" };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = parseFrames(buffer);
      buffer = rest;

      for (const event of events) {
        yield event;
      }
    }

    // Flush any complete frame left in the buffer at stream end.
    const { events } = parseFrames(buffer + "\n\n");
    for (const event of events) {
      yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

export async function fetchHealth(): Promise<{
  status: string;
  database: string;
  llm: string;
  embedding_model: string;
  ingested_years: { survey_year: number; n_variables: number }[];
} | null> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/health`);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export { API_BASE_URL };
