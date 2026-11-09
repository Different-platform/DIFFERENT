// Shared domain types for the DIFFER frontend.
//
// These mirror the backend's SSE contract (services/api/events.py and
// domain.py) exactly — same field names, snake_case — so the real SSE reader
// drops into the UI without touching a component, and the ported components
// from the Vite template keep working unchanged.

export type Verdict = "MEASURED" | "RELATED" | "ABSENT";
export type RouteIntent = "codebook" | "data" | "both";

export interface Citation {
  variable_name: string;
  section: string;
  page: number;
  survey_year: number;
  description?: string;
  question_text?: string;
}

export interface SqlResult {
  sql: string;
  verified: boolean;
  problems: string[];
}

export interface NumericResult {
  columns: string[];
  rows: (string | number)[][];
  weight_variable: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  verdict?: Verdict;
  sql?: SqlResult;
  result?: NumericResult;
  route?: RouteIntent;
  phase?: "routing" | "streaming" | "done";
  error?: string;
}

// The backend supports 2019-2023 (survey microdata loaded). 2017-2018 are
// defined in the spec but not ingested (no value-label codebooks), so the
// selector only offers what the backend can actually answer.
export const SURVEY_YEARS = [2019, 2020, 2021, 2022, 2023] as const;
