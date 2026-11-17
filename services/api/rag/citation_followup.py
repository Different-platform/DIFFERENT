"""Is the newest message about variables already cited, or a new question?

Mirrors `rag/followup.py`, which answers the same question for a previously
*computed statistic*. This module covers the other shape of prior answer: a
codebook citation with no SQL (a `MEASURED` codebook answer, or a `RELATED`
"closest variable" answer).

Without this classification, a question like "which one do researchers use?"
or "why doesn't that one count?" was rewritten into a standalone question and
pushed back through retrieval and the presence judge — stages that never saw
the specific variable the question was actually about, and can land on a
different, merely-similar one. Two concrete failures motivated this:

- "Which of those is the one most researchers use?" was judged ABSENT,
  because "which variable do researchers prefer" is not itself something
  NHIS measures — the judge was asked the wrong question entirely.
- "Why doesn't that one count as measuring it?" came back describing a
  *different* variable (RJCLSSD1_A) than the one just named (RJWRKCLSSD_A),
  because the rewritten question had no lexical anchor forcing retrieval back
  to the specific item already on screen.

Kept as a separate module rather than folded into `followup.py`'s classifier:
that classifier's prompt and examples are tuned specifically for computed
statistics ("explain how this was calculated", "how do I cite this"), and
mixing in codebook-citation examples would make every call pay for both sets
of examples even when only one applies, for no accuracy benefit.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from services.api.llm.gateway import LLMGateway, LLMGatewayError, Message

logger = logging.getLogger(__name__)

CitationFollowUpKind = Literal["about_citations", "new"]
_KINDS: tuple[str, ...] = ("about_citations", "new")

SYSTEM_PROMPT = """\
A health researcher is chatting with an assistant that answers questions \
about the U.S. National Health Interview Survey (NHIS) codebook. The \
assistant just named one or more specific codebook variables in its answer, \
without computing a statistic. Classify the researcher's NEWEST message.

"about_citations": reasons about the variable(s) just named, without asking \
about a new concept. This includes: asking which of the named variables is \
better, more common, or more often used; asking why a named variable does or \
does not count as measuring something; asking for the page, year, section, \
or other detail of a variable already named; asking for the difference \
between two variables already named. Examples: "which of those is the one \
most researchers use", "so which variable is closest", "why doesn't that one \
count as measuring it", "what page is that on", "what's the difference \
between those two".

"new": asks about a different concept the assistant has not yet addressed, \
even if phrased as a follow-up. Examples: "how is poverty measured", "does it \
also ask about children", "what about disability".

Respond with JSON only: {"kind": "about_citations"} or {"kind": "new"}\
"""

#: A deterministic backstop for when the classifier call fails. Narrow on
#: purpose: a false positive here would answer a genuinely new question with
#: reasoning about the wrong variable, which is the exact bug this exists to
#: prevent, just relocated rather than fixed.
_ABOUT_CITATIONS_CUE = re.compile(
    r"\b(?:that one|those|this one|which one|which (?:of (?:those|these)|"
    r"variable)|what page|what year|what section|why (?:doesn'?t|does not|"
    r"isn'?t|is not)|the difference between|more (?:common(?:ly)?|used|"
    r"popular))\b",
    re.IGNORECASE,
)


def looks_like_about_citations(question: str) -> bool:
    """True only for wording that plainly refers back to a named variable."""
    return bool(_ABOUT_CITATIONS_CUE.search(question or ""))


def _parse(raw: str) -> CitationFollowUpKind:
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return "new"
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return "new"
    if not isinstance(payload, dict):
        return "new"
    kind = payload.get("kind")
    if isinstance(kind, str) and kind.strip().lower() in _KINDS:
        return kind.strip().lower()  # type: ignore[return-value]
    return "new"


async def classify(
    question: str,
    prior_question: str,
    prior_answer: str,
    *,
    gateway: LLMGateway,
) -> CitationFollowUpKind:
    """What the newest message wants, given a previous codebook citation.

    A classifier failure falls back to the keyword backstop; anything still
    unclear is "new", the safe default — the normal pipeline can still answer
    it, just by searching afresh rather than assuming it is about what was
    already named.
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
            stage="citation_followup",
        )
        kind = _parse(response.text)
    except LLMGatewayError:
        logger.warning(
            "citation follow-up classification failed; using the keyword backstop"
        )
        kind = "new"
    if kind == "new" and looks_like_about_citations(question):
        return "about_citations"
    return kind
