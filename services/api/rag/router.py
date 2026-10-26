"""Intent routing (Requirement 15.1).

Decides whether a question wants a codebook explanation, a computed number,
or both. This is a fixed branch in the pipeline, not an agent choosing its
own next action — the question shapes are enumerable, so a classification
call plus an if/else covers them, and the path stays comparable across runs
for the eval baseline.
"""

from __future__ import annotations

import json
import logging

from services.api.domain import RouteIntent
from services.api.llm.gateway import LLMGateway, LLMGatewayError, Message

logger = logging.getLogger(__name__)

VALID_INTENTS: frozenset[str] = frozenset({"codebook", "data", "both"})

SYSTEM_PROMPT = """\
Classify what a health researcher's question about the U.S. National Health \
Interview Survey (NHIS) is asking for.

codebook - asks what the survey measures, how a question was worded, what a \
variable means, which codes it uses, or who was asked it. Answerable from the \
codebook alone.

data - asks for a computed figure from respondent data: a mean, percentage, \
rate, count, distribution, or crosstab.

both - asks what is measured AND for a figure computed from it.

Respond with JSON only:
{"intent": "codebook" | "data" | "both"}\
"""


def _parse_intent(raw: str) -> RouteIntent | None:
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        lowered = raw.lower()
        for candidate in ("both", "codebook", "data"):
            if candidate in lowered:
                return candidate  # type: ignore[return-value]
        return None

    intent = str(payload.get("intent", "")).strip().lower()
    return intent if intent in VALID_INTENTS else None  # type: ignore[return-value]


async def classify(question: str, *, gateway: LLMGateway) -> RouteIntent:
    """Classify a question's intent, defaulting to `codebook`.

    `codebook` is the safe default: it answers from cited codebook content.
    Defaulting to `data` would push a question toward generated SQL and a
    number, which is the path where being wrong is most costly.
    """
    try:
        response = await gateway.complete(
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=f"Question: {question}")],
            temperature=0.0,
            response_format="json",
            stage="router",
        )
    except LLMGatewayError:
        raise

    intent = _parse_intent(response.text)
    if intent is None:
        logger.warning("router returned an unparseable intent: %r", response.text[:200])
        return "codebook"
    return intent
