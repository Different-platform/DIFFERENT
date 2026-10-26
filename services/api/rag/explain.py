"""Explain a statistic that was already computed.

The explanation is grounded in what actually ran: the saved SQL, the summary
that was shown, and the codebook details of the variables the SQL used. The
model is told to describe, not to compute, and not to introduce figures.

Telling a model not to invent numbers is not a control. This is a product
whose promise is "no number unless it was verified", so the text is checked:
every number in the explanation must already appear in the material it was
given (or be a small count or a survey year). A failing explanation gets one
retry with the offending numbers named, then falls back to the summary that
was already shown. The explanation is buffered rather than streamed from the
model for exactly this reason; the orchestrator re-chunks it for the UI.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from services.api.domain import Hit
from services.api.llm.gateway import LLMGateway, Message

SYSTEM_PROMPT = """\
You explain to a health researcher how a statistic you already computed from \
U.S. National Health Interview Survey (NHIS) data was produced.

You are given the question, the summary you already gave, the exact SQL that \
ran, and codebook details for the variables it used. Write two or three short \
paragraphs of plain prose covering:
- what the figure means and which population it describes;
- how it was calculated, step by step, read from the SQL (name the formula if \
there is one, such as BMI = 703 x weight in pounds / height in inches squared);
- why respondents with missing-data codes were left out, and which codes;
- what the survey weight does and why an unweighted figure would be wrong;
- one or two honest limits of the measure, only where they are real (for \
example self-reported height and weight, or a figure derived by formula rather \
than published as a single NHIS variable).

Rules:
- Use only facts in the material provided. Do not state any number that is \
not in it: no sample sizes, no other statistics, no page numbers.
- Do not recompute or restate the result with different precision.
- The statistic WAS computed. Never say it cannot be computed.
- No headings, no bullet lists.\
"""

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: Words in generated SQL that are not variable names.
_SQL_WORDS = frozenset(
    "SELECT FROM WHERE GROUP BY ORDER AND OR NOT IN AS SUM AVG COUNT CASE WHEN "
    "THEN ELSE END OVER PARTITION NULL IS BETWEEN LIMIT DESC ASC WITH HAVING "
    "DISTINCT COALESCE NULLIF ROUND CAST NUMERIC FLOAT WTFA_A WTFA_SA PSTRAT PPSU".split()
)


def year_of(sql: str) -> int | None:
    """The survey year of the table a query ran against."""
    match = re.search(r"\bsurvey_(\d{4})\b", sql, re.IGNORECASE)
    return int(match.group(1)) if match else None


def variable_names(sql: str) -> list[str]:
    """Upper-case identifiers in `sql` that could be NHIS variables, in order."""
    names: list[str] = []
    for token in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", sql):
        if token in _SQL_WORDS or re.fullmatch(r"SURVEY_\d{4}", token):
            continue
        if token not in names:
            names.append(token)
    return names


def _numbers(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER.findall(text or ""):
        try:
            found.add(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return found


def ungrounded_numbers(text: str, source: str) -> list[str]:
    """Numbers in `text` that are not in `source`.

    Small whole numbers (0-10: "two steps", "four categories") and survey
    years are allowed, because ordinary prose needs them and they cannot pass
    for a finding.
    """
    allowed = _numbers(source)
    allowed |= {Decimal(n) for n in range(0, 11)}
    allowed |= {Decimal(y) for y in range(2017, 2024)}
    bad: list[str] = []
    for token in _NUMBER.findall(text or ""):
        try:
            value = Decimal(token.replace(",", ""))
        except InvalidOperation:
            continue
        if value not in allowed and token not in bad:
            bad.append(token)
    return bad


def _code(value) -> str:
    return str(int(value)) if value == value.to_integral_value() else str(value)


def describe_variable(hit: Hit) -> str:
    lines = [f"{hit.variable_name}: {hit.description or '(no description)'}"]
    if hit.universe_description:
        lines.append(f"  Asked of: {hit.universe_description}")
    if hit.is_recode:
        lines.append("  A recode derived from other variables.")
    missing = [f"{_code(vl.code)} = {vl.label}" for vl in hit.sentinel_labels]
    if missing:
        lines.append("  Missing-data codes: " + "; ".join(missing))
    return "\n".join(lines)


def build_prompt(
    question: str,
    summary: str,
    sql: str,
    variables: list[Hit],
    feedback: str | None = None,
) -> str:
    details = "\n".join(describe_variable(v) for v in variables) or "(none)"
    prompt = (
        f"The researcher asked: {question}\n\n"
        f"Summary you already gave:\n{summary}\n\n"
        f"The exact SQL that ran:\n{sql}\n\n"
        f"Codebook details for the variables it used:\n{details}\n\n"
        f"Explain how this statistic was produced."
    )
    if feedback:
        prompt += f"\n\n{feedback}"
    return prompt


def fallback(summary: str) -> str:
    """Used when no grounded explanation can be produced."""
    return (
        f"{summary} The query shown above is exactly what produced this figure: "
        f"it weights every respondent by the NHIS survey weight (WTFA_A) and "
        f"leaves out the missing-data codes named in the summary."
    )


async def compose(
    *,
    question: str,
    summary: str,
    sql: str,
    variables: list[Hit],
    gateway: LLMGateway,
) -> str:
    """A grounded explanation, or the original summary if none can be made."""
    source = "\n".join(
        [question, summary, sql, *[describe_variable(v) for v in variables]]
    )
    feedback: str | None = None
    for _ in range(2):
        response = await gateway.complete(
            system=SYSTEM_PROMPT,
            messages=[
                Message(
                    role="user",
                    content=build_prompt(question, summary, sql, variables, feedback),
                )
            ],
            temperature=0.2,
            stage="explain",
        )
        text = response.text.strip()
        bad = ungrounded_numbers(text, source)
        if text and not bad:
            return text
        if bad:
            feedback = (
                "Your previous explanation contained numbers that are not in "
                f"the material above ({', '.join(bad)}). Rewrite it using only "
                "numbers that appear in the material."
            )
    return fallback(summary)
