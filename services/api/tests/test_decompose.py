"""Decomposing a compound research question into separate concepts."""

from __future__ import annotations

import pytest

from services.api.llm.gateway import FakeLLMGateway, LLMGatewayError
from services.api.rag import decompose

COMPOUND_QUESTION = (
    "I want to answer the research question of whether differences in rates "
    "of serious psychological distress between blacks and whites changed "
    "after the COVID19 pandemic when telehealth visits became reimbursable "
    "as opposed to before the pandemic, accounting for known differences in "
    "Blacks and whites in terms of educational attainment, marital rates, "
    "and insurance type."
)


# --- looks_compound: the cheap gate -----------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        COMPOUND_QUESTION,
        "I'm interested in determining if CMS's expansion of telehealth "
        "reimbursement in March 2020 meaningfully changed racial "
        "inequalities in health. Is this something I can study in the NHIS?",
        "Can you compare food insecurity to depression rates accounting for "
        "insurance type?",
    ],
)
def test_long_or_connective_questions_look_compound(question):
    assert decompose.looks_compound(question)


@pytest.mark.parametrize(
    "question",
    [
        "What is the average BMI in 2023?",
        "Does NHIS measure telehealth use?",
        "How is food insecurity measured?",
        "What about 2021?",
    ],
)
def test_short_single_concept_questions_do_not_look_compound(question):
    assert not decompose.looks_compound(question)


# --- parsing ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "fallback", "expected"),
    [
        ('{"concepts": ["race", "insurance type"]}', "q", ["race", "insurance type"]),
        ('{"concepts": ["only one"]}', "q", ["only one"]),
        ('```json\n{"concepts": ["a", "b"]}\n```', "q", ["a", "b"]),
        ('{"concepts": []}', "q", ["q"]),
        ('{"concepts": "not a list"}', "q", ["q"]),
        ("not json", "q", ["q"]),
        ("", "q", ["q"]),
    ],
)
def test_parse(raw, fallback, expected):
    assert decompose._parse(raw, fallback) == expected


def test_parse_caps_at_max_concepts():
    raw = '{"concepts": ["a", "b", "c", "d", "e", "f", "g"]}'
    result = decompose._parse(raw, "q")
    assert len(result) == decompose.MAX_CONCEPTS
    assert result == ["a", "b", "c", "d", "e"]


# --- decompose() end to end ---------------------------------------------------
async def test_a_single_concept_question_returns_itself_unchanged():
    gateway = FakeLLMGateway(responses=['{"concepts": ["How is BMI measured?"]}'])
    result = await decompose.decompose("How is BMI measured?", gateway=gateway)
    assert result == ["How is BMI measured?"]


def test_prompt_tells_the_model_to_normalize_policy_framing():
    """Regression guard for the professor's telehealth-reimbursement question.

    Decomposing "CMS's expansion of telehealth reimbursement in March 2020"
    into a concept that keeps that policy phrasing embeds nowhere near NHIS's
    plain-language variable descriptions (VIRAPP12M_A, "Virtual medical
    appointment, past 12m"), so the judge sees no matching candidate and the
    system wrongly claims NHIS does not measure telehealth at all. The system
    prompt must steer the model to translate policy/program framing into the
    underlying measurable concept instead of preserving it verbatim.
    """
    assert "policy" in decompose.SYSTEM_PROMPT.lower()
    assert "measurable" in decompose.SYSTEM_PROMPT.lower()


async def test_the_motivating_compound_question_splits_into_its_concepts():
    gateway = FakeLLMGateway(
        responses=[
            '{"concepts": ["serious psychological distress", '
            '"race (Black vs White)", "health insurance coverage type", '
            '"educational attainment", "marital status"]}'
        ]
    )
    result = await decompose.decompose(COMPOUND_QUESTION, gateway=gateway)
    assert "serious psychological distress" in result
    assert len(result) <= decompose.MAX_CONCEPTS


class _Broken:
    async def complete(self, **kwargs):
        raise LLMGatewayError("down")


async def test_a_gateway_failure_degrades_to_one_concept():
    result = await decompose.decompose(COMPOUND_QUESTION, gateway=_Broken())
    assert result == [COMPOUND_QUESTION]
