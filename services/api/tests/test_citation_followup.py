"""Classifying a follow-up about an already-cited codebook variable.

Mirrors test_explain.py's classifier tests, for the codebook-citation shape
of prior answer instead of a computed statistic.
"""

from __future__ import annotations

import pytest

from services.api.llm.gateway import FakeLLMGateway, LLMGatewayError
from services.api.rag import citation_followup

PRIOR_QUESTION = "How is food insecurity measured?"
PRIOR_ANSWER = (
    "NHIS captures this with FDSCAT4_A, a four-category food-security recode, "
    "and FDSCAT3_A, a three-category version."
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"kind": "about_citations"}', "about_citations"),
        ('{"kind": "new"}', "new"),
        ('{"kind": "About_Citations "}', "about_citations"),
        ('```json\n{"kind": "about_citations"}\n```', "about_citations"),
        ('{"kind": "maybe"}', "new"),
        ("not json at all", "new"),
        ("", "new"),
    ],
)
def test_parse(raw, expected):
    assert citation_followup._parse(raw) == expected


class _Broken:
    async def complete(self, **kwargs):
        raise LLMGatewayError("down")


async def test_classifier_failure_falls_back_to_the_keyword_backstop():
    """The exact question that was wrongly judged ABSENT."""
    kind = await citation_followup.classify(
        "Which of those is the one most researchers use?",
        PRIOR_QUESTION,
        PRIOR_ANSWER,
        gateway=_Broken(),
    )
    assert kind == "about_citations"


async def test_classifier_failure_on_a_genuinely_new_question_defaults_to_new():
    kind = await citation_followup.classify(
        "How is poverty measured?", PRIOR_QUESTION, PRIOR_ANSWER, gateway=_Broken()
    )
    assert kind == "new"


async def test_a_missed_reference_is_caught_by_the_backstop():
    """The exact follow-up that used to drift to a different variable."""
    gateway = FakeLLMGateway(responses=['{"kind": "new"}'])
    kind = await citation_followup.classify(
        "Why doesn't that one count as measuring it?",
        PRIOR_QUESTION,
        PRIOR_ANSWER,
        gateway=gateway,
    )
    assert kind == "about_citations"


async def test_the_classifier_choice_is_kept_when_it_is_not_new():
    gateway = FakeLLMGateway(responses=['{"kind": "about_citations"}'])
    kind = await citation_followup.classify(
        "So which variable is closest?", PRIOR_QUESTION, PRIOR_ANSWER, gateway=gateway
    )
    assert kind == "about_citations"


@pytest.mark.parametrize(
    "message",
    [
        "Which of those is the one most researchers use?",
        "So which variable is closest?",
        "Why doesn't that one count as measuring it?",
        "What page is that on?",
        "What's the difference between those two?",
        "Is RJWRKCLSSD_A more commonly used?",
    ],
)
def test_backstop_recognises_a_reference_to_what_was_named(message):
    assert citation_followup.looks_like_about_citations(message)


@pytest.mark.parametrize(
    "message",
    [
        "How is poverty measured?",
        "Does NHIS ask about disability?",
        "What about children?",
    ],
)
def test_backstop_leaves_new_concepts_alone(message):
    assert not citation_followup.looks_like_about_citations(message)
