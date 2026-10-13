import type { Conversation } from "./types";

/**
 * All codebook content below is invented placeholder data, not real NHIS figures.
 * Field names mirror the real API contract so a real backend can be wired in later.
 */

function daysAgoIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString();
}

export const MOCK_CONVERSATIONS: Conversation[] = [
  // c1 — MEASURED, route: codebook
  {
    id: "c1",
    title: "How is food insecurity measured?",
    default_year: 2023,
    created_at: daysAgoIso(1),
    messages: [
      {
        id: "c1-u1",
        role: "user",
        content: "How is food insecurity measured?",
      },
      {
        id: "c1-a1",
        role: "assistant",
        content:
          "NHIS measures adult food security with the 10-item USDA Adult Food Security Survey Module, asked about the past 30 days. Responses are summarized in FDSCAT4_A, which classifies adults as high, marginal, low, or very low food security. Individual items such as FDSRUNOUT_A are also available if you need item-level analysis.",
        route: "codebook",
        verdict: "MEASURED",
        phase: "done",
        citations: [
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
        ],
      },
    ],
  },

  // c2 — MEASURED, route: both, verified SQL + result
  {
    id: "c2",
    title: "Weighted mean BMI, 2023",
    default_year: 2023,
    created_at: daysAgoIso(2),
    messages: [
      {
        id: "c2-u1",
        role: "user",
        content: "What's the weighted mean BMI for adults in 2023?",
      },
      {
        id: "c2-a1",
        role: "assistant",
        content:
          "The weighted mean BMI among U.S. adults in 2023 was 28.47, after excluding the BMI_A sentinel code 99.9 (Unknown). Estimates use the final annual adult weight WTFA_A.",
        route: "both",
        verdict: "MEASURED",
        phase: "done",
        citations: [
          {
            variable_name: "BMI_A",
            section: "Height and Weight",
            page: 88,
            survey_year: 2023,
            description:
              "Body mass index, computed from self-reported height and weight.",
            question_text: "Derived. 99.9 = Unknown.",
          },
        ],
        sql: {
          sql: `SELECT
  SUM(BMICAT_A_VAL * WTFA_A) / SUM(WTFA_A) AS weighted_mean_bmi
FROM nhis_adult_2023
WHERE BMI_A NOT IN (99.9)   -- 99.9 = Unknown (sentinel for BMI_A, 2023)
  AND BMI_A IS NOT NULL;`,
          verified: true,
          problems: [],
        },
        result: {
          columns: ["Statistic", "Value", "Unweighted n"],
          rows: [["Weighted mean BMI, adults 18+", 28.47, 27651]],
          weight_variable: "WTFA_A",
        },
      },
    ],
  },

  // c3 — MEASURED, route: data, FAILED verification
  {
    id: "c3",
    title: "Hispanic origin breakdown",
    default_year: 2023,
    created_at: daysAgoIso(4),
    messages: [
      {
        id: "c3-u1",
        role: "user",
        content: "Give me the weighted distribution of HISPALLP_A in 2023.",
      },
      {
        id: "c3-a1",
        role: "assistant",
        content:
          "I generated a query for this, but it did not pass verification against the 2023 codebook, so no number is shown. The query excludes codes that are not defined for HISPALLP_A and keeps a refusal code as a real category. Please rephrase or I can retry with the codebook's sentinel list.",
        route: "data",
        verdict: "MEASURED",
        phase: "done",
        citations: [
          {
            variable_name: "HISPALLP_A",
            section: "Demographics",
            page: 31,
            survey_year: 2023,
            description:
              "Single and multiple race groups with Hispanic origin.",
            question_text:
              "Derived from HISP_A and RACEALLP_A. 97 Refused, 98 Not ascertained, 99 Don\u2019t know.",
          },
        ],
        sql: {
          sql: `SELECT HISPALLP_A, SUM(WTFA_A) AS weighted_n
FROM nhis_adult_2023
WHERE HISPALLP_A NOT IN (77, 99)
GROUP BY HISPALLP_A;`,
          verified: false,
          problems: [
            "Code 77 is not a defined value for HISPALLP_A in 2023 (defined sentinels: 97, 98, 99).",
            "Code 97 (Refused) would be counted as a substantive category.",
          ],
        },
      },
    ],
  },

  // c4 — RELATED, route: codebook
  {
    id: "c4",
    title: "Loneliness measures",
    default_year: null,
    created_at: daysAgoIso(7),
    messages: [
      {
        id: "c4-u1",
        role: "user",
        content: "Does NHIS measure loneliness?",
      },
      {
        id: "c4-a1",
        role: "assistant",
        content:
          "NHIS does not ask directly about loneliness. The closest variable is SUPPORT_A, which asks how often the adult gets the social and emotional support they need. This measures perceived support, not the subjective feeling of being lonely, so treat it as a related proxy rather than an equivalent measure.",
        route: "codebook",
        verdict: "RELATED",
        phase: "done",
        citations: [
          {
            variable_name: "SUPPORT_A",
            section: "Social Support",
            page: 211,
            survey_year: 2021,
            description:
              "How often adult gets the social and emotional support needed.",
            question_text:
              "How often do you get the social and emotional support you need? Always, usually, sometimes, rarely, or never?",
          },
        ],
      },
    ],
  },

  // c5 — ABSENT, route: codebook, no citations
  {
    id: "c5",
    title: "Firearm ownership",
    default_year: null,
    created_at: daysAgoIso(7),
    messages: [
      {
        id: "c5-u1",
        role: "user",
        content: "Does NHIS measure firearm ownership?",
      },
      {
        id: "c5-a1",
        role: "assistant",
        content:
          "Not measured in NHIS. No variable in the 2017\u20132023 public-use codebooks records household firearm ownership, so I can't compute this statistic.",
        route: "codebook",
        verdict: "ABSENT",
        phase: "done",
      },
    ],
  },

  // c6 — MEASURED, route: both, verified crosstab
  {
    id: "c6",
    title: "Uninsured rate by sex, 2022",
    default_year: 2022,
    created_at: daysAgoIso(2),
    messages: [
      {
        id: "c6-u1",
        role: "user",
        content: "What's the uninsured rate by sex in 2022?",
      },
      {
        id: "c6-a1",
        role: "assistant",
        content:
          "In 2022, 10.4% of adult men and 7.9% of adult women were uninsured at the time of interview (NOTCOV_A = 1).",
        route: "both",
        verdict: "MEASURED",
        phase: "done",
        citations: [
          {
            variable_name: "NOTCOV_A",
            section: "Health Insurance",
            page: 164,
            survey_year: 2022,
            description: "Coverage status as used in Health United States.",
            question_text: "1 Not covered, 2 Covered, 9 Don\u2019t know.",
          },
        ],
        sql: {
          sql: `SELECT SEX_A,
  SUM(CASE WHEN NOTCOV_A = 1 THEN WTFA_A ELSE 0 END) / SUM(WTFA_A) AS pct_uninsured
FROM nhis_adult_2022
WHERE NOTCOV_A IN (1, 2) AND SEX_A IN (1, 2)
GROUP BY SEX_A;`,
          verified: true,
          problems: [],
        },
        result: {
          columns: ["Sex", "% Uninsured", "Unweighted n"],
          rows: [
            ["Male", "10.4%", 12988],
            ["Female", "7.9%", 14663],
          ],
          weight_variable: "WTFA_A",
        },
      },
    ],
  },
];

export const EMPTY_EXAMPLE_QUESTIONS: string[] = [
  "How is food insecurity measured?",
  "Does NHIS measure loneliness?",
  "What's the weighted mean BMI for adults in 2023?",
  "Give me the weighted distribution of HISPALLP_A in 2023.",
];
