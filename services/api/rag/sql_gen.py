"""Generate analytical SQL with real codes injected (Requirement 4.1).

The first of the two findings about SQL: given thin context, the model invents
missing-data codes. The fix is not a better prompt asking it to be careful — it
is to **inject the variable's actual value-label codes into the context**, so
the correct codes are present rather than recalled. The verifier (`verify.py`)
is the second gate that catches the cases where injection is not enough.

This stage emits a single read-only SELECT against one `survey_YYYY` table,
using the real survey column names (a variable's survey column is its codebook
name) and the real final-annual-weight column. It does not execute anything —
generation, verification, and execution are three separate steps precisely so a
number can be blocked between generation and execution.
"""

from __future__ import annotations

import re

from services.api.domain import Hit
from services.api.llm.gateway import LLMGateway, Message

# NHIS Sample Adult final annual weight and design variables, 2019+. The weight
# is what makes a mean population-representative; the generator must select it
# alongside the measure so the analysis engine can weight the result.
WEIGHT_COLUMN = "WTFA_A"
DESIGN_COLUMNS = ("PSTRAT", "PPSU")

SYSTEM_PROMPT = """\
You write a single PostgreSQL SELECT that computes a descriptive statistic from \
one year of U.S. National Health Interview Survey (NHIS) Sample Adult microdata.

Rules, all mandatory:
- Output ONE read-only SELECT statement. No INSERT/UPDATE/DELETE/DDL, no \
semicolons beyond the single statement, no comments.
- Query only the one table named in the context (survey_<year>).
- Use ONLY the exact column names and numeric codes given in the context. Never \
invent a code.
- Missing data: for EVERY column your query uses, add a predicate \
`<column> NOT IN (<every missing-data code listed for that column>)` to the \
WHERE clause, listing all of its codes. Use no other numeric filters (no <, >, \
BETWEEN) unless the question asks about one specific category.
- The estimate MUST be population-weighted by WTFA_A and computed in SQL. Never \
return raw per-respondent rows.
  * Weighted mean of a column X: \
    SELECT SUM(X * WTFA_A) / SUM(WTFA_A) AS weighted_mean FROM survey_<year> \
    WHERE X NOT IN (<X codes>)
  * Derived measure: when the question asks for a quantity no single column \
holds but a standard formula defines it from columns in the context, compute \
the formula inside the weighted mean. BMI from height in inches and weight in \
pounds is 703 * weight / (height * height), so: \
    SELECT SUM(703.0 * WEIGHT / (HEIGHT * HEIGHT) * WTFA_A) / SUM(WTFA_A) AS \
    weighted_mean_bmi FROM survey_<year> WHERE HEIGHT NOT IN (<HEIGHT codes>) \
    AND WEIGHT NOT IN (<WEIGHT codes>) \
    Use a formula only when no column already holds the measure, and only a \
standard one.
  * Distribution of a categorical column C: \
    SELECT C, SUM(WTFA_A) / (SELECT SUM(WTFA_A) FROM survey_<year> WHERE C NOT \
    IN (<C codes>)) AS proportion FROM survey_<year> WHERE C NOT IN (<C codes>) \
    GROUP BY C ORDER BY C
  * Share in one category (for example "what percent are obese"): \
    SELECT SUM(CASE WHEN C = <code> THEN WTFA_A ELSE 0 END) / SUM(WTFA_A) AS \
    proportion FROM survey_<year> WHERE C NOT IN (<C codes>)
  * Breakdown of C by a second categorical column D ("by sex", "for each \
group"): report C's distribution WITHIN each D, so each D group sums to 100%: \
    SELECT D, C, SUM(WTFA_A) / SUM(SUM(WTFA_A)) OVER (PARTITION BY D) AS \
    proportion FROM survey_<year> WHERE C NOT IN (<C codes>) AND D NOT IN \
    (<D codes>) GROUP BY D, C ORDER BY D, C
  * Mean of a column X within each category of D: \
    SELECT D, SUM(X * WTFA_A) / SUM(WTFA_A) AS weighted_mean FROM \
    survey_<year> WHERE X NOT IN (<X codes>) AND D NOT IN (<D codes>) GROUP BY \
    D ORDER BY D
- Give aggregates clear aliases (weighted_mean, proportion, weighted_count). \
Do not round or format.

Respond with JSON only: {"sql": "<the SELECT statement>"}\
"""


def _table_name(survey_year: int) -> str:
    return f"survey_{survey_year}"


def format_schema(hits: list[Hit], survey_year: int) -> str:
    """Describe the available columns and their real codes for the prompt.

    Only the retrieved variables are offered as columns, so the generator
    cannot reference a variable that was never surfaced. Each variable lists
    its substantive codes and, separately, its sentinel codes — the exact
    information whose absence causes invented codes.
    """
    lines = [
        f"Table: {_table_name(survey_year)} (one row per respondent, "
        f"survey year {survey_year})",
        f"Weight column (always include): {WEIGHT_COLUMN} (continuous, do not filter by code)",
        f"Design columns (continuous, do not filter by code): {', '.join(DESIGN_COLUMNS)}",
        "",
        "Measure / category columns available:",
    ]
    for hit in hits:
        col = hit.variable_name
        desc = hit.description or "(no description)"
        lines.append(f"- {col}: {desc}")
        if hit.data_type:
            lines.append(f"    type: {hit.data_type}")
        substantive = [
            f"{_code(vl.code)}={vl.label}" for vl in hit.substantive_labels
        ]
        if substantive:
            lines.append("    valid codes: " + "; ".join(substantive))
        else:
            lines.append("    valid codes: continuous (range of values)")
        sentinels = [f"{_code(vl.code)}" for vl in hit.sentinel_labels]
        if sentinels:
            lines.append(
                "    missing-data codes to EXCLUDE: " + ", ".join(sentinels)
            )
    return "\n".join(lines)


def _code(value) -> str:
    try:
        return str(int(value)) if value == value.to_integral_value() else str(value)
    except (AttributeError, ValueError):
        return str(value)


def build_prompt(
    question: str,
    hits: list[Hit],
    survey_year: int,
    feedback: list[str] | None = None,
) -> str:
    prompt = (
        f"Question: {question}\n\n"
        f"Available schema and codes:\n\n{format_schema(hits, survey_year)}\n\n"
        f"Write the SELECT that answers the question."
    )
    if feedback:
        problems = "\n".join(f"- {line}" for line in feedback)
        prompt += (
            "\n\nYour previous attempt was rejected by the verifier for these "
            f"reasons:\n{problems}\nWrite a corrected SELECT that fixes every "
            "one of them."
        )
    return prompt


def _extract_sql(raw: str) -> str:
    """Pull the SELECT out of a JSON (or fenced, or bare) model response."""
    text = raw.strip()

    # Prefer a JSON object with an "sql" field.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        import json

        try:
            payload = json.loads(text[start : end + 1])
            if isinstance(payload, dict) and payload.get("sql"):
                return str(payload["sql"]).strip()
        except json.JSONDecodeError:
            pass

    # Fenced code block.
    fence = re.search(r"```(?:sql)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()

    # Fall back to the first SELECT/WITH onward.
    select = re.search(r"\b(select|with)\b.*", text, re.DOTALL | re.IGNORECASE)
    if select:
        return select.group(0).strip().rstrip(";").strip()

    return text.rstrip(";").strip()


async def generate(
    question: str,
    survey_year: int,
    hits: list[Hit],
    *,
    gateway: LLMGateway,
    feedback: list[str] | None = None,
) -> str:
    """Return a single SELECT string. It is NOT verified or executed here.

    `feedback` carries the verifier's reasons for rejecting a previous attempt,
    so a repair attempt can fix them. The repaired SQL still has to pass the
    verifier like any other; feedback makes a pass likelier, never unnecessary.
    """
    response = await gateway.complete(
        system=SYSTEM_PROMPT,
        messages=[
            Message(
                role="user",
                content=build_prompt(question, hits, survey_year, feedback),
            )
        ],
        temperature=0.0,
        response_format="json",
        stage="sql_gen",
    )
    return _extract_sql(response.text)
