/**
 * Single entry point the console uses to ask a question.
 *
 * Picks the real API or the local mock from one env flag, so the UI can still
 * be developed offline against the mock while the real backend is the default.
 */

import { apiAsk, type AskOptions } from "./api-stream";
import { mockAsk, type StreamEvent } from "./mock-stream";

/** Set VITE_USE_MOCK=true to work against local mock data instead of the API. */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

export function ask(
  question: string,
  options: AskOptions = {},
): AsyncGenerator<StreamEvent, void, unknown> {
  return USE_MOCK ? mockAsk(question) : apiAsk(question, options);
}

export type { AskOptions, StreamEvent };
