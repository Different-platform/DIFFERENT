"""Stage-appropriate responses for the fake gateway.

Used when no GROQ_API_KEY is configured, so the whole pipeline — retrieval,
judging, citation grounding, SSE streaming — can be exercised against real
ingested data without a provider account.

This is explicitly a stub, not a model. It applies keyword rules rather than
reasoning, and it is reported as `llm: "stub"` by /api/health so a stub answer
can never be mistaken for a real one. Only the judging and wording are faked;
the variables, citations, pages, and sentinel flags are all real data read
from Postgres.
"""

from __future__ import annotations

import json
import re

from services.api.llm.gateway import Message

# Concepts NHIS genuinely does not measure in the 2017-2023 public-use files.
_ABSENT_PATTERNS = re.compile(
    r"\b(gun|guns|firearm|firearms|weapon|weapons|"
    r"income tax|crypto|voting|vote|religio)\w*", re.I
)

# Concepts with only an adjacent proxy in NHIS.
_RELATED_PATTERNS = re.compile(r"\b(lonel|isolat|happiness|wellbeing|well-being)\w*", re.I)

# Phrasing that asks for a number rather than a definition.
_DATA_PATTERNS = re.compile(
    r"\b(mean|average|median|rate|percent|percentage|proportion|share|"
    r"how many|count|distribution|crosstab|cross-tab|compare|trend)\w*", re.I
)

_VARIABLE_TOKEN = re.compile(r"\b([A-Z][A-Z0-9]{2,}(?:_[A-Z])?)\b")


def _user_text(messages: list[Message]) -> str:
    return "\n".join(m.content for m in messages if m.role == "user")


def _question_from(messages: list[Message]) -> str:
    """Pull the question out of a prompt that may also carry context."""
    text = _user_text(messages)
    match = re.search(r"^Question:\s*(.+)$", text, re.M)
    return match.group(1).strip() if match else text.strip()


def _candidate_variables(messages: list[Message], limit: int = 3) -> list[str]:
    """Variable names appearing in the retrieved context block."""
    text = _user_text(messages)
    seen: list[str] = []
    for token in _VARIABLE_TOKEN.findall(text):
        if token in {"MEASURED", "RELATED", "ABSENT", "NHIS", "SQL"}:
            continue
        if token not in seen:
            seen.append(token)
    return seen[:limit]


def _rewrite(messages: list[Message]) -> str:
    # The stub cannot resolve pronouns; returning the question unchanged is
    # the honest no-op, and the orchestrator treats rewriting as optional.
    return _question_from(messages)


def _route(messages: list[Message]) -> str:
    question = _question_from(messages)
    intent = "data" if _DATA_PATTERNS.search(question) else "codebook"
    return json.dumps({"intent": intent})


def _judge(messages: list[Message]) -> str:
    question = _question_from(messages)
    candidates = _candidate_variables(messages, limit=1)
    closest = candidates[0] if candidates else None

    if _ABSENT_PATTERNS.search(question):
        return json.dumps(
            {
                "verdict": "ABSENT",
                "why": (
                    "No variable in the 2017-2023 NHIS public-use codebooks "
                    "records this concept."
                ),
            }
        )

    if _RELATED_PATTERNS.search(question):
        return json.dumps(
            {
                "verdict": "RELATED",
                "why": (
                    f"NHIS has no direct measure of this. The closest variable "
                    f"is {closest}, which captures something adjacent rather "
                    f"than the concept asked about."
                    if closest
                    else "NHIS has only an adjacent proxy for this concept."
                ),
            }
        )

    return json.dumps(
        {
            "verdict": "MEASURED",
            "why": f"{closest} directly captures this concept."
            if closest
            else "A retrieved variable directly captures this concept.",
        }
    )


def _generate(messages: list[Message]) -> str:
    question = _question_from(messages)
    variables = _candidate_variables(messages, limit=3)

    if not variables:
        return (
            "I could not find a variable in the NHIS codebook that measures "
            "this, so I have nothing to report."
        )

    primary = variables[0]
    others = variables[1:]

    sentences = [
        f"NHIS measures this with {primary}, according to the "
        f"{_year_from(messages)} Sample Adult codebook."
    ]
    if others:
        sentences.append(
            "Related variables in the same section include "
            + ", ".join(others)
            + "."
        )
    sentences.append(
        "See the citations below for the exact codebook page and the "
        "response options recorded for each variable."
    )
    del question
    return " ".join(sentences)


def _year_from(messages: list[Message]) -> str:
    match = re.search(r"\b(20\d{2})\b", _user_text(messages))
    return match.group(1) if match else "2023"


_HANDLERS = {
    "rewrite": _rewrite,
    "router": _route,
    "judge": _judge,
    "generate": _generate,
}


def stub_handler(
    system: str, messages: list[Message], stage: str | None
) -> str:
    """Dispatch to a stage-appropriate canned response."""
    del system
    handler = _HANDLERS.get(stage or "")
    if handler is None:
        return ""
    return handler(messages)
