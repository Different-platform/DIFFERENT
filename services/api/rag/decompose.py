"""Break a compound research question into its separable measurable concepts.

A real researcher's question routinely bundles several distinct NHIS
concepts into one sentence — race, a time window, insurance type,
educational attainment, a health outcome. Retrieval embeds the *whole*
standalone question as one vector, and that average dilutes every single
concept's match quality. Two concrete failures motivated this module,
found by testing the exact questions a professor sent as evaluation
material:

- "...whether differences in rates of serious psychological distress
  between blacks and whites changed after the COVID19 pandemic when
  telehealth visits became reimbursable..." embedded as one vector ranks
  K6SPD_A (the actual NHIS "serious psychological distress" scale) outside
  the top 10 candidates. The identical phrase "serious psychological
  distress" asked alone ranks K6SPD_A #1 at similarity 0.61 vs. 0.41 buried
  in the compound sentence. The system then told the researcher NHIS does
  not measure psychological distress at all — false, and it had even named
  the K6 scale by name while saying so.
- "Does NHIS measure telehealth use?" retrieves VIRAPP12M_A (the actual
  telehealth-visit variable) at rank 5, inside the candidate pool the judge
  sees, but outside the top-3 a flat similarity-ranked citation list keeps —
  so the prose correctly discusses it while the citation cards shown to the
  researcher omit it entirely.

The fix: decompose into concepts, retrieve for each independently, and let
the orchestrator merge and judge per concept. A single-concept question is
unaffected — `looks_compound()` gates the extra LLM call so the common case
costs nothing extra and behaves exactly as before.
"""

from __future__ import annotations

import json
import logging
import re

from services.api.llm.gateway import LLMGateway, LLMGatewayError, Message

logger = logging.getLogger(__name__)

#: Hard cap regardless of what the model returns. Each extra concept costs
#: one more retrieval round-trip and one more judge call, so this bounds
#: both latency and LLM spend for a single question.
MAX_CONCEPTS = 5

SYSTEM_PROMPT = """\
A health researcher asked a question about the U.S. National Health \
Interview Survey (NHIS). Break it into the distinct concepts it asks about \
that NHIS might have a specific variable or small group of variables for — \
things like a health condition or behavior, a demographic group, an \
insurance or coverage type, a time period, or an educational or marital \
characteristic.

Phrase each concept as the plain, measurable thing a household health \
survey would actually ask someone ("telehealth use", "virtual medical \
visit"), not the policy, program, or administrative framing the researcher \
used to explain why they care about it ("CMS's expansion of telehealth \
reimbursement", "the March 2020 policy change"). Surveys ask about what \
happened to a person, not about the regulation that made it possible — a \
policy or program name alone is not something NHIS has a variable for, so \
translate it to the underlying behavior, condition, or circumstance it \
would show up as in survey data.

Return each concept as a short phrase, naming ONE concept each. Do not \
merge unrelated concepts into one phrase, and do not split a single \
concept into several near-duplicates.

If the question already asks about only one concept, return a list \
containing just that one question, unchanged.

Return at most 5 concepts.

Respond with JSON only: {"concepts": ["concept one", "concept two", ...]}\
"""

#: A cheap pre-filter so the common, single-concept question never pays for
#: the decompose() call at all. Tuned to the examples that motivated this
#: module: compound questions are long and carry a clause connecting
#: multiple ideas; "What is the average BMI in 2023?" has neither.
_COMPOUND_CUE = re.compile(
    r"\b(?:whether|accounting for|as opposed to|compared? (?:to|with)|"
    r"differences? between|controlling for|adjust(?:ed|ing) for|and whether)\b",
    re.IGNORECASE,
)

#: Above this length a question is long enough that it plausibly bundles
#: more than one concept even without an explicit connecting word.
_LONG_QUESTION_WORDS = 18


def looks_compound(question: str) -> bool:
    """Should this question even be sent to `decompose()`?

    A false negative here just means a compound question is treated as one
    concept, degrading to today's behavior rather than failing outright. A
    false positive costs one extra LLM call for a question that turns out
    to be single-concept anyway, which `decompose()` itself then returns
    unchanged.
    """
    text = question or ""
    return len(text.split()) > _LONG_QUESTION_WORDS or bool(_COMPOUND_CUE.search(text))


def _parse(raw: str, fallback: str) -> list[str]:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return [fallback]
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return [fallback]
    concepts = payload.get("concepts") if isinstance(payload, dict) else None
    if not isinstance(concepts, list) or not concepts:
        return [fallback]
    cleaned = [c.strip() for c in concepts if isinstance(c, str) and c.strip()]
    return cleaned[:MAX_CONCEPTS] if cleaned else [fallback]


async def decompose(question: str, *, gateway: LLMGateway) -> list[str]:
    """The distinct measurable concepts `question` asks about.

    Falls back to `[question]` on any failure — identical to not having
    decomposed at all, which is always a safe degradation, just a less
    precise one.
    """
    try:
        response = await gateway.complete(
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=f"Question: {question}")],
            temperature=0.0,
            response_format="json",
            stage="decompose",
        )
    except LLMGatewayError:
        logger.warning("decomposition failed; treating the question as one concept")
        return [question]
    return _parse(response.text, question)
