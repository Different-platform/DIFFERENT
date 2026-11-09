"""Follow-up rewriting (Requirement 1.2).

"What about for women?" is meaningless to a vector index. Rewriting it into
"How is food insecurity measured for women?" before retrieval is what makes
multi-turn conversation work at all.
"""

from __future__ import annotations

import logging

from services.api.llm.gateway import LLMGateway, LLMGatewayError, Message

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Rewrite a health researcher's follow-up question into a standalone question \
that can be understood with no conversation history.

Rules:
- Resolve pronouns and elliptical references using the history provided.
- Preserve the researcher's terminology; do not substitute synonyms for \
survey or clinical terms.
- Do not answer the question or add detail the researcher did not ask for.
- A follow-up modifies the previous question. Keep every part the follow-up \
does not change (the measure, the population, the year) and replace only the \
part it does change. Asking for something "categorically" or "by category" \
replaces a mean with a distribution across categories; naming a year or group \
replaces the year or group.
- If the question already stands alone, return it unchanged.

Examples:
History: Researcher: What is the weighted mean BMI for adults in 2023?
Follow-up: Can you do it categorically?
Standalone: What is the distribution of BMI categories among adults in 2023?

History: Researcher: What percent of adults have diabetes in 2022?
Follow-up: What about 2020?
Standalone: What percent of adults have diabetes in 2020?

History: Researcher: What percent of adults are obese in 2023?
Follow-up: Break it down by sex.
Standalone: What percent of adults are obese in 2023, broken down by sex?

Return only the rewritten question, with no preamble or quotation marks.\
"""

#: Turns of history to include. Bounded deliberately: the whole transcript
#: would grow the prompt without bound and cost scales per question.
HISTORY_TURNS = 6


def _format_history(history: list[Message]) -> str:
    recent = history[-HISTORY_TURNS:]
    return "\n".join(
        f"{'Researcher' if m.role == 'user' else 'Assistant'}: {m.content}"
        for m in recent
    )


async def to_standalone(
    question: str,
    history: list[Message],
    *,
    gateway: LLMGateway,
) -> str:
    """Rewrite `question` into a standalone form.

    Falls back to the original question on any failure. A rewrite is an
    optimisation, not a correctness requirement — degrading to the literal
    question is far better than failing the request.
    """
    if not history:
        return question

    prompt = (
        f"Conversation so far:\n{_format_history(history)}\n\n"
        f"Follow-up question: {question}\n\n"
        f"Standalone question:"
    )

    try:
        response = await gateway.complete(
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=prompt)],
            temperature=0.0,
            stage="rewrite",
        )
    except LLMGatewayError:
        logger.warning("rewrite failed; falling back to the original question")
        return question

    rewritten = response.text.strip().strip('"').strip()
    return rewritten or question
