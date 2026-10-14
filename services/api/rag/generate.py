"""Grounded answer composition (Requirements 1.3, 2.1, 2.2).

Citations are derived from retrieved hits, never from the generated text.
That ordering is deliberate and resolves a real tension: the UI renders
citations *before* the streamed answer (01-ui-requirements.md §4), while
filtering citations by what the answer mentions would require buffering the
entire answer first and would kill token streaming.

Building citations from the retrieved set makes Requirement 2.2 hold by
construction — a variable that was never retrieved cannot be cited, because
the citation list is a subset of the retrieval result. `ground_citations` is
retained for the non-streaming path and for testing the filter directly.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from services.api.domain import Citation, Hit, Verdict
from services.api.llm.gateway import LLMGateway, Message

#: NHIS variable names are upper-case, 3+ chars, usually suffixed `_A`.
_VARIABLE_TOKEN = re.compile(r"\b([A-Z][A-Z0-9]{2,}(?:_[A-Z])?)\b")

#: Upper-case tokens that are not variable names.
_NOT_VARIABLES: frozenset[str] = frozenset(
    {
        "MEASURED",
        "RELATED",
        "ABSENT",
        "NHIS",
        "SQL",
        "USDA",
        "JSON",
        "API",
        "US",
        "USA",
        "NOT",
        "AND",
        "THE",
    }
)

DEFAULT_MAX_CITATIONS = 3

SYSTEM_PROMPT = """\
You answer questions from health and social science researchers about what the \
U.S. National Health Interview Survey (NHIS) measures.

Ground every statement in the codebook variables provided. Specifically:
- Name the variables you rely on, using their exact codebook names.
- Never mention a variable that is not in the provided context.
- Never invent response codes, categories, or question wording.
- Where it matters, say who was asked the question (the universe), because a \
variable asked of a conditional subsample is easy to misread as covering \
everyone.
- If the provided variables only partly answer the question, say what is \
missing rather than filling the gap.

Write two to four sentences of plain prose for an expert reader. No bullet \
lists, no headings, no restatement of the question.\
"""


def extract_variable_mentions(text: str) -> set[str]:
    """Variable-looking tokens appearing in free text."""
    return {
        token
        for token in _VARIABLE_TOKEN.findall(text or "")
        if token not in _NOT_VARIABLES
    }


def citations_from_hits(
    hits: list[Hit], *, limit: int = DEFAULT_MAX_CITATIONS
) -> list[Citation]:
    """Citations for the top retrieved hits.

    Every citation carries variable name, section, page, and survey year
    (Requirement 2.1) because `Hit.to_citation` populates all four from the
    stored codebook record.
    """
    return [hit.to_citation() for hit in hits[:limit]]


def ground_citations(
    hits: list[Hit], answer_text: str, *, limit: int = DEFAULT_MAX_CITATIONS
) -> list[Citation]:
    """Citations for variables the answer mentions *and* retrieval returned.

    Implements the filter behind Property 1: the result is always a subset of
    the retrieved set, so a hallucinated variable name in the answer can never
    become a citation. Falls back to the top hits when the answer names
    nothing recognisable, so a reader is never left without provenance.
    """
    retrieved = {hit.variable_name: hit for hit in hits}
    mentioned = extract_variable_mentions(answer_text)

    grounded = [retrieved[name] for name in mentioned if name in retrieved]
    if not grounded:
        return citations_from_hits(hits, limit=limit)

    grounded.sort(key=lambda hit: hit.similarity, reverse=True)
    return [hit.to_citation() for hit in grounded[:limit]]


def format_context(hits: list[Hit], *, max_labels: int = 10) -> str:
    """Render retrieved variables as answer context."""
    blocks: list[str] = []
    for hit in hits:
        lines = [
            f"{hit.variable_name} - {hit.description or '(no description)'}",
            f"  Section: {hit.section_name or 'unknown'} "
            f"(page {hit.page}, survey year {hit.survey_year})",
        ]
        if hit.question_text:
            lines.append(f"  Survey question: {hit.question_text}")
        if hit.universe_description:
            lines.append(f"  Asked of: {hit.universe_description}")
        if hit.is_recode:
            lines.append("  This variable is a recode derived from other variables.")

        labels = [vl.label for vl in hit.substantive_labels][:max_labels]
        if labels:
            lines.append("  Response options: " + "; ".join(labels))

        sentinels = [f"{vl.code}={vl.label}" for vl in hit.sentinel_labels]
        if sentinels:
            lines.append("  Missing-data codes: " + "; ".join(sentinels))

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks) if blocks else "(no variables retrieved)"


def build_prompt(question: str, hits: list[Hit], verdict: Verdict) -> str:
    guidance = {
        "MEASURED": (
            "These variables do measure the concept asked about. Explain how "
            "it is measured."
        ),
        "RELATED": (
            "NHIS does NOT directly measure the concept asked about. Present "
            "the closest variable below and state plainly how it differs from "
            "what was asked, so it is not mistaken for an equivalent measure."
        ),
        "ABSENT": (
            "NHIS does NOT measure the concept asked about. Say so plainly and "
            "do not offer these variables as substitutes."
        ),
    }[verdict]

    return (
        f"Question: {question}\n\n"
        f"Presence check: {verdict}. {guidance}\n\n"
        f"NHIS codebook variables retrieved for this question:\n\n"
        f"{format_context(hits)}"
    )


def not_measured_message(question: str, why: str) -> str:
    """Answer for an ABSENT verdict (Requirement 3.2).

    Written without an LLM call: there is nothing to ground an answer in, and
    a model given an empty context is exactly where invention happens. A
    direct refusal is the correct answer, not a failure.
    """
    del question
    reason = why.strip() or (
        "No variable in the 2017-2023 NHIS public-use codebooks records this "
        "concept."
    )
    return (
        f"Not measured in NHIS. {reason} Because the survey does not ask "
        f"about it, there is no variable to cite and no statistic I can "
        f"compute for it."
    )


def statistics_unavailable_message(hits: list[Hit]) -> str:
    """Honest response when a question wants a number this version cannot compute.

    SQL generation, verification, and the weighted-statistics engine are a
    later phase. Returning a clear limitation is the only acceptable
    behaviour — the alternative is an unverified number, which is precisely
    what this product exists to prevent.
    """
    names = ", ".join(hit.variable_name for hit in hits[:2])
    suffix = f" The relevant variables appear to be {names}." if names else ""
    return (
        "This question asks for a computed statistic, which this version "
        "cannot produce yet — generated SQL is not verified against the "
        "codebook until a later phase, and an unverified number is worse "
        f"than none.{suffix} I can explain what the variables measure in the "
        "meantime."
    )


async def compose(
    question: str,
    hits: list[Hit],
    verdict: Verdict,
    *,
    gateway: LLMGateway,
) -> AsyncIterator[str]:
    """Stream a grounded answer token by token (Requirement 1.3)."""
    async for chunk in gateway.stream(
        system=SYSTEM_PROMPT,
        messages=[Message(role="user", content=build_prompt(question, hits, verdict))],
        temperature=0.2,
        stage="generate",
    ):
        yield chunk
