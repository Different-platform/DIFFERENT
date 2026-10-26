"""Presence judge (Requirement 3.1, 3.4).

The first of the two safety gates. Decides whether the retrieved candidates
actually *are* what the researcher asked about:

    MEASURED  a retrieved variable directly captures the concept
    RELATED   something adjacent exists, but it is not the same construct
    ABSENT    NHIS does not measure this concept

A similarity threshold cannot make this call. Measured and unmeasured
concepts overlap completely in the score distribution — in prototyping a real
question scored *lower* than every absent one. Cosine similarity measures
topical relatedness, not construct equivalence, so the decision needs a model
reasoning about meaning. That change took absence detection from 0/4 to 4/4.

Observable in the ingested 2023 data: "household firearm ownership" retrieves
HHRESPSA_FLG, HRFIRE12M_A and HHX at ~0.42 — plausible-looking neighbours for
a concept the survey never asks about.
"""

from __future__ import annotations

import json
import logging

from services.api.domain import Hit, Verdict
from services.api.llm.gateway import LLMGateway, LLMGatewayError, Message

logger = logging.getLogger(__name__)

VALID_VERDICTS: frozenset[str] = frozenset({"MEASURED", "RELATED", "ABSENT"})

SYSTEM_PROMPT = """\
You judge whether the U.S. National Health Interview Survey (NHIS) actually \
measures the concept a health researcher asked about.

You are given the researcher's question and the codebook variables retrieved \
for it. Decide which single verdict applies:

MEASURED - one of the retrieved variables directly captures the concept asked \
about. The variable and the question refer to the same construct.

RELATED - the retrieved variables cover an adjacent or overlapping topic, but \
none of them measures the specific construct asked about. Use this when a \
researcher could mistake the closest variable for the thing they wanted.

ABSENT - NHIS does not measure this concept at all. The retrieved variables \
are topically unrelated to the question; they surfaced only because vector \
search always returns its nearest neighbours.

A concept also counts as MEASURED when it can be computed from the retrieved \
variables by a standard, unambiguous formula, even if no single variable holds \
it. Example: body mass index is defined as weight and height combined, which \
is exactly how NHIS builds its own BMI categories, so height plus weight \
variables measure BMI. Name the variables it is derived from in "why". Do not \
stretch this to loose associations: the formula must define the concept \
itself, and every input it needs must be among the retrieved variables.

Judge construct equivalence, not topical similarity. Retrieval scores are \
provided for context only; a high score does not make a variable the right \
answer, and a low score does not make it wrong. Being candid that NHIS lacks \
a measure is a correct and useful answer, never a failure.

Respond with JSON only:
{"verdict": "MEASURED" | "RELATED" | "ABSENT", "why": "<one or two sentences>"}\
"""


def format_candidates(hits: list[Hit], max_labels: int = 8) -> str:
    """Render candidates for the prompt, including response options.

    Response options are included because they are often what distinguishes a
    screening instrument from a diagnosis, or a 30-day recall from a lifetime
    one — distinctions the verdict turns on.
    """
    if not hits:
        return "(no variables were retrieved)"

    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        lines = [
            f"{index}. {hit.variable_name} - {hit.description or '(no description)'}",
            f"   Section: {hit.section_name or 'unknown'} "
            f"(page {hit.page}, {hit.survey_year})",
            f"   Retrieval score: {hit.similarity:.3f}",
        ]
        if hit.question_text:
            lines.append(f"   Survey question: {hit.question_text}")
        if hit.universe_description:
            lines.append(f"   Asked of: {hit.universe_description}")

        labels = [vl.label for vl in hit.substantive_labels][:max_labels]
        if labels:
            lines.append("   Response options: " + "; ".join(labels))
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def build_prompt(question: str, hits: list[Hit]) -> str:
    return (
        f"Question: {question}\n\n"
        f"Retrieved NHIS codebook variables:\n\n{format_candidates(hits)}"
    )


def _parse_verdict(raw: str) -> tuple[Verdict | None, str]:
    """Extract (verdict, why) from a model response, tolerating stray prose."""
    text = raw.strip()
    if not text:
        return None, ""

    # Models sometimes wrap JSON in fences or commentary.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        upper = raw.upper()
        for candidate in ("ABSENT", "RELATED", "MEASURED"):
            if candidate in upper:
                return candidate, ""  # type: ignore[return-value]
        return None, ""

    verdict = str(payload.get("verdict", "")).strip().upper()
    why = str(payload.get("why", "")).strip()
    if verdict in VALID_VERDICTS:
        return verdict, why  # type: ignore[return-value]
    return None, why


async def classify_presence(
    question: str,
    hits: list[Hit],
    *,
    gateway: LLMGateway,
) -> tuple[Verdict, Hit | None, str]:
    """Return (verdict, closest_hit, rationale).

    `closest_hit` is the top-ranked candidate, which the orchestrator presents
    for a RELATED verdict and withholds for ABSENT.

    With nothing retrieved the verdict is ABSENT without consulting the model:
    there is no construct to compare against, and an LLM call could only
    hallucinate one.
    """
    if not hits:
        return "ABSENT", None, "No codebook variables were retrieved for this question."

    closest = hits[0]

    try:
        response = await gateway.complete(
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=build_prompt(question, hits))],
            temperature=0.0,
            response_format="json",
            stage="judge",
        )
    except LLMGatewayError:
        raise

    verdict, why = _parse_verdict(response.text)

    if verdict is None:
        # Refusing to guess: an unparseable judgement is reported as RELATED,
        # which presents the closest variable *with* a caveat rather than
        # asserting a match that was never actually confirmed.
        logger.warning(
            "presence judge returned an unparseable verdict: %r", response.text[:200]
        )
        return (
            "RELATED",
            closest,
            "The presence check did not return a usable verdict, so this "
            "variable is offered as the closest match rather than a "
            "confirmed one.",
        )

    return verdict, (None if verdict == "ABSENT" else closest), why
