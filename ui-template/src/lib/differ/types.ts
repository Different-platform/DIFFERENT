export type Verdict = "MEASURED" | "RELATED" | "ABSENT";
export type RouteIntent = "codebook" | "data" | "both";

export interface Citation {
  variable_name: string;
  section: string;
  page: number;
  survey_year: number;
  /** UI-only mock extras for the expanded card */
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
  /** UI-only streaming state */
  route?: RouteIntent;
  phase?: "routing" | "streaming" | "done";
}

export interface Conversation {
  id: string;
  title: string;
  default_year: number | null;
  created_at: string;
  messages: Message[];
}

// Only years whose data is loaded in the backend. 2017-2018 are in the spec
// but were never ingested (no value-label codebooks were available), so
// offering them would only produce answers that say "nothing found".
export const SURVEY_YEARS = [2019, 2020, 2021, 2022, 2023] as const;
