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
- Do not answer the question, add detail, or infer intent beyond what was said.
- If the question already stands alone, return it unchanged.

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
