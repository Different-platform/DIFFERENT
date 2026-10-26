"""Is the newest message asking about the previous answer, or a new question?

"Explain me in detail" is not a question about NHIS. It is a request about the
answer the assistant just gave. Sent through the normal pipeline it is
rewritten into "explain how the weighted mean BMI is calculated", retrieved
afresh, and judged afresh, which can contradict the answer it is meant to
explain: the pipeline never sees the SQL, formula, or result it just produced.

So when the last turn was a computed statistic, a small classification call
decides whether the new message wants that answer explained. If so it is
answered from the saved computation (rag/explain.py). Otherwise it flows
through the normal pipeline like any other follow-up ("what about 2021?",
"can you do it categorically?").

Only asked when there is a computed answer to explain, so conversations
without one pay nothing.
"""

from __future__ import annotations

import json
import logging

from services.api.llm.gateway import LLMGateway, LLMGatewayError, Message

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
A health researcher is chatting with an assistant that answers questions \
about the U.S. National Health Interview Survey (NHIS). The assistant just \
gave a computed statistic. Decide whether the researcher's NEWEST message asks \
the assistant to EXPLAIN that answer, or is something else.

explain = true when the message asks to explain, justify, elaborate on, or \
show the working behind the previous answer: "explain in detail", "how did you \
calculate that", "why that formula", "what does this mean", "walk me through \
it", "show your work", "can you elaborate".

explain = false when it asks for something new or a change to the question: a \
different measure, year, group, breakdown, or format. "what about 2021", \
"can you do it categorically", "break it down by sex", "now for women", \
"how is food insecurity measured" are all false.

Respond with JSON only: {"explain": true} or {"explain": false}\
"""


def _parse(raw: str) -> bool:
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return False
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return False
    return payload.get("explain") is True


async def asks_for_explanation(
    question: str,
    prior_question: str,
    prior_answer: str,
    *,
    gateway: LLMGateway,
) -> bool:
    """True when `question` asks to explain the previous computed answer.

    Any failure returns False: the message then takes the normal pipeline,
    which is the safe default (it can only answer, never invent a result).
    """
    prompt = (
        f"Previous question: {prior_question}\n\n"
        f"Assistant's previous answer: {prior_answer[:700]}\n\n"
        f"Researcher's newest message: {question}"
    )
    try:
        response = await gateway.complete(
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=prompt)],
            temperature=0.0,
            response_format="json",
            stage="followup",
        )
    except LLMGatewayError:
        logger.warning("follow-up classification failed; using the normal pipeline")
        return False
    return _parse(response.text)
