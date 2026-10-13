import type {
  Citation,
  NumericResult,
  RouteIntent,
  SqlResult,
  Verdict,
} from "./types";

export type StreamEvent =
  | { type: "route"; route: RouteIntent }
  | { type: "verdict"; verdict: Verdict }
  | { type: "citations"; citations: Citation[] }
  | { type: "sql"; sql: SqlResult }
  | { type: "result"; result: NumericResult } // only when sql.verified
  | { type: "token"; text: string }
  | { type: "done" };

interface Scenario {
  route: RouteIntent;
  verdict: Verdict;
  content: string;
  citations?: Citation[];
  sql?: SqlResult;
  result?: NumericResult;
}

const BMI_CITATION: Citation = {
  variable_name: "BMI_A",
  section: "Height and Weight",
  page: 88,
  survey_year: 2023,
  description: "Body mass index, computed from self-reported height and weight.",
  question_text: "Derived. 99.9 = Unknown.",
};

const BMI_SQL: SqlResult = {
  sql: `SELECT
  SUM(BMICAT_A_VAL * WTFA_A) / SUM(WTFA_A) AS weighted_mean_bmi
FROM nhis_adult_2023
WHERE BMI_A NOT IN (99.9)   -- 99.9 = Unknown (sentinel for BMI_A, 2023)
  AND BMI_A IS NOT NULL;`,
  verified: true,
  problems: [],
};

const BMI_RESULT: NumericResult = {
  columns: ["Statistic", "Value", "Unweighted n"],
  rows: [["Weighted mean BMI, adults 18+", 28.47, 27651]],
  weight_variable: "WTFA_A",
};

const HISPALLP_CITATION: Citation = {
  variable_name: "HISPALLP_A",
  section: "Demographics",
  page: 31,
  survey_year: 2023,
  description: "Single and multiple race groups with Hispanic origin.",
  question_text:
    "Derived from HISP_A and RACEALLP_A. 97 Refused, 98 Not ascertained, 99 Don\u2019t know.",
};

const HISPALLP_SQL: SqlResult = {
  sql: `SELECT HISPALLP_A, SUM(WTFA_A) AS weighted_n
FROM nhis_adult_2023
WHERE HISPALLP_A NOT IN (77, 99)
GROUP BY HISPALLP_A;`,
  verified: false,
  problems: [
    "Code 77 is not a defined value for HISPALLP_A in 2023 (defined sentinels: 97, 98, 99).",
    "Code 97 (Refused) would be counted as a substantive category.",
  ],
};

const SUPPORT_CITATION: Citation = {
  variable_name: "SUPPORT_A",
  section: "Social Support",
  page: 211,
  survey_year: 2021,
  description: "How often adult gets the social and emotional support needed.",
  question_text:
    "How often do you get the social and emotional support you need? Always, usually, sometimes, rarely, or never?",
};

const FOOD_SECURITY_CITATIONS: Citation[] = [
  {
    variable_name: "FDSCAT4_A",
    section: "Food Security",
    page: 142,
    survey_year: 2023,
    description:
      "Adult food security category, 30-day, 4 levels (high, marginal, low, very low).",
    question_text:
      "Derived from the 10-item USDA Adult Food Security Survey Module (FDSLAST_A … FDSNEDAYS_A).",
  },
  {
    variable_name: "FDSRUNOUT_A",
    section: "Food Security",
    page: 139,
    survey_year: 2023,
    description:
      "Worried food would run out before getting money to buy more, past 30 days.",
    question_text:
      "\u2018The food that we bought just didn\u2019t last, and we didn\u2019t have money to get more.\u2019 Was that often, sometimes, or never true in the last 30 days?",
  },
];

/**
 * Pick a canned scenario for a live question, based on keyword matching.
 * See 03-mock-data.md §4 for the documented table this implements.
 */
export function pickScenario(question: string): Scenario {
  const q = question.toLowerCase();

  if (/(gun|firearm|weapon)/.test(q)) {
    return {
      route: "codebook",
      verdict: "ABSENT",
      content:
        "Not measured in NHIS. No 2017\u20132023 public-use variable records this concept, so I won't estimate it.",
    };
  }

  if (/(lonel|isolat)/.test(q)) {
    return {
      route: "codebook",
      verdict: "RELATED",
      content:
        "There's no direct loneliness item. The closest variable is SUPPORT_A (perceived social and emotional support), which is related but measures support, not loneliness itself.",
      citations: [SUPPORT_CITATION],
    };
  }

  if (/hisp/.test(q)) {
    return {
      route: "data",
      verdict: "MEASURED",
      content:
        "The generated query failed codebook verification, so no number is shown. See the problems listed above.",
      citations: [HISPALLP_CITATION],
      sql: HISPALLP_SQL,
    };
  }

  if (/(mean|average|rate|percent|crosstab|bmi|share|how many)/.test(q)) {
    return {
      route: "both",
      verdict: "MEASURED",
      content:
        "The weighted mean BMI among adults was 28.47, excluding the sentinel code 99.9 (Unknown), weighted by WTFA_A.",
      citations: [BMI_CITATION],
      sql: BMI_SQL,
      result: BMI_RESULT,
    };
  }

  return {
    route: "codebook",
    verdict: "MEASURED",
    content:
      "NHIS measures this with the USDA Adult Food Security Survey Module over the past 30 days, summarized in FDSCAT4_A with item-level detail in variables like FDSRUNOUT_A.",
    citations: FOOD_SECURITY_CITATIONS,
  };
}

function delay(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

/**
 * Simulated streaming "API". Emits events in the documented order:
 * route -> verdict -> citations -> sql -> result -> token* -> done
 *
 * This is the single swap point for a real SSE reader — UI code only
 * ever consumes `StreamEvent`s, never this function's internals.
 */
export async function* mockAsk(
  question: string,
): AsyncGenerator<StreamEvent, void, unknown> {
  const scenario = pickScenario(question);

  await delay(1100);
  yield { type: "route", route: scenario.route };

  await delay(350);
  yield { type: "verdict", verdict: scenario.verdict };

  if (scenario.citations && scenario.citations.length > 0) {
    await delay(350);
    yield { type: "citations", citations: scenario.citations };
  }

  if (scenario.sql) {
    await delay(350);
    yield { type: "sql", sql: scenario.sql };

    if (scenario.sql.verified && scenario.result) {
      await delay(600);
      yield { type: "result", result: scenario.result };
    }
  }

  const tokens = scenario.content.split(/(\s+)/).filter((t) => t.length > 0);
  for (const token of tokens) {
    await delay(22);
    yield { type: "token", text: token };
  }

  yield { type: "done" };
}
