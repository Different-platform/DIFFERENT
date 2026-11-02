"""Is the newest message about the previous answer, or a new question?

"Explain me in detail" and "how can I use this in my research?" are not
questions about NHIS. They are requests about the answer the assistant just
gave. Sent through the normal pipeline they are rewritten into a standalone
question, retrieved afresh, judged afresh, and, for a statistic, recomputed:
"how can I use this BMI in my research?" re-ran the BMI query and repeated the
same number, which answered nothing that was asked.

So when the last turn was a computed statistic, a small classification call
sorts the new message into one of three kinds:

- ``explain``: how the figure was produced. Answered from the saved
  computation (rag/explain.py).
- ``guidance``: how to use, interpret, report, or cite the figure in research,
  and what its limits are. Answered from the saved computation plus fixed,
  checkable research notes (rag/guidance.py).
- ``new``: anything that changes the question (a year, group, breakdown, or
  measure). Takes the normal pipeline like any other follow-up.

Only asked when there is a computed answer to talk about, so conversations
without one pay nothing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from services.api.llm.gateway import LLMGateway, LLMGatewayError, Message

logger = logging.getLogger(__name__)

FollowUpKind = Literal["explain", "guidance", "new"]
_KINDS: tuple[str, ...] = ("explain", "guidance", "new")

SYSTEM_PROMPT = """\
A health researcher is chatting with an assistant that answers questions \
about the U.S. National Health Interview Survey (NHIS). The assistant just \
gave a computed statistic. Classify the researcher's NEWEST message.

"explain": asks how the previous figure was produced: "explain in detail", \
"how did you calculate that", "why that formula", "walk me through it", \
"show your work", "can you elaborate".

"guidance": asks how to USE, interpret, report, cite, or rely on the previous \
figure, or what its limits are, without asking for a different computation: \
"how can I use this in my research", "what does this mean for my study", \
"how should I interpret this", "is this good enough for a paper", "how do I \
cite this", "what are the limitations", "can I compare this with other \
studies".

"new": asks for something new or a change to the question: a different \
measure, year, group, breakdown, or format. "what about 2021", "can you do it \
categorically", "break it down by sex", "now for women", "how is food \
insecurity measured" are all "new".

Respond with JSON only: {"kind": "explain"}, {"kind": "guidance"} or \
{"kind": "new"}\
"""

#: Wording that asks about using or interpreting a result.
_GUIDANCE_CUE = re.compile(
    r"\b(?:my|our)\s+(?:research|study|paper|analysis|thesis|dissertation|"
    r"project|report|manuscript|article)\b"
    r"|\bhow\s+(?:can|could|should|do|would|might)\s+(?:i|we)\s+"
    r"(?:use|interpret|report|cite|present|apply)\b"
    r"|\b(?:interpret\w*|limitations?|caveats?)\b",
    re.IGNORECASE,
)

#: Wording that asks for a different computation. Any of these keeps the
#: message on the normal pipeline even if it also mentions "my research".
_NEW_COMPUTATION_CUE = re.compile(
    r"\b(?:19|20)\d{2}\b"
    r"|\bbreak\w*\s+(?:it|this|that|them)?\s*down\b"
    r"|\bby\s+(?:sex|gender|age|race|ethnicity|region|state|income|education|"
    r"year|group)\b"
    r"|\b(?:for|among)\s+(?:women|men|females|males|children|seniors|"
    r"older|younger)\b"
    r"|\b(?:compare|versus|vs|instead|categor\w*|distribution|trend)\b",
    re.IGNORECASE,
)


def looks_like_guidance(question: str) -> bool:
    """A deterministic backstop for the classifier.

    True only for a message that asks about using or interpreting a result and
    asks for no new computation. Kept narrow on purpose: a false positive here
    would answer "break it down by sex for my study" with advice instead of
    numbers.
    """
    text = question or ""
    return bool(_GUIDANCE_CUE.search(text)) and not _NEW_COMPUTATION_CUE.search(text)


def _parse(raw: str) -> FollowUpKind:
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
    # The older two-way shape, in case a model answers in it.
    if payload.get("explain") is True:
        return "explain"
    return "new"


async def classify(
    question: str,
    prior_question: str,
    prior_answer: str,
    *,
    gateway: LLMGateway,
) -> FollowUpKind:
    """What the newest message wants, given a previous computed answer.

    A classifier failure or a "new" verdict on a message that plainly asks
    for research guidance falls back to `looks_like_guidance`; anything else
    unclear is "new", which is the safe default (the normal pipeline can only
    answer, never invent a result).
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
        kind = _parse(response.text)
    except LLMGatewayError:
        logger.warning("follow-up classification failed; using the keyword backstop")
        kind = "new"
    if kind == "new" and looks_like_guidance(question):
        return "guidance"
    return kind
