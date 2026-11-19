"""Answering a follow-up about already-cited codebook variables.

Mirrors test_explain.py's grounding tests, checking variable *names* instead
of numbers — the risk this module exists to close.
"""

from __future__ import annotations

from services.api.domain import Citation, Hit
from services.api.llm.gateway import FakeLLMGateway
from services.api.rag import cite_explain

PRIOR_QUESTION = "Does NHIS measure loneliness or social isolation?"
PRIOR_ANSWER = (
    "The closest NHIS item is RJWRKCLSSD_A, which asks how often a worker "
    "had to stay within six feet of others during COVID-19 social distancing. "
    "It captures workplace proximity, not personal feelings of loneliness."
)

RJWRKCLSSD_A = Hit(
    variable_name="RJWRKCLSSD_A",
    survey_year=2020,
    page=576,
    similarity=0.6,
    section_name="Social distancing at work",
    description="Work close to others with social distancing in effect",
)

CITATIONS = [
    Citation(
        variable_name="RJWRKCLSSD_A",
        section="Social distancing at work",
        page=576,
        survey_year=2020,
    )
]


# --- year_availability_answer -------------------------------------------
def test_year_availability_question_is_answered_from_the_db_lookup_not_guessed():
    """"Is that available every year from 2019 to 2023?" has a real, known
    answer (which survey_year rows exist for this variable name) that the
    system has every fact needed for. Before this existed, the question fell
    through to an LLM reasoning step given no year data at all, which came
    back with "I'd need to check the codebooks" — a non-answer for something
    already knowable from the database."""
    years_by_variable = {"SLPHOURS_A": [2020, 2022]}
    answer = cite_explain.year_availability_answer(
        CITATIONS, "Is that available every year from 2019 to 2023?", years_by_variable
    )
    assert answer is None  # CITATIONS here cites RJWRKCLSSD_A, not SLPHOURS_A

    citations = [
        Citation(variable_name="SLPHOURS_A", section="Sleep", page=516, survey_year=2020)
    ]
    answer = cite_explain.year_availability_answer(
        citations, "Is that available every year from 2019 to 2023?", years_by_variable
    )
    assert answer == "SLPHOURS_A is available in: 2020, 2022."


def test_year_availability_cue_does_not_fire_on_the_single_year_question():
    """"What year is that from" is the existing single-year metadata_answer's
    job; this must not also claim it, or the two could disagree."""
    answer = cite_explain.year_availability_answer(
        CITATIONS, "What year is that from?", {"RJWRKCLSSD_A": [2020]}
    )
    assert answer is None

GOOD = (
    "RJWRKCLSSD_A does not count because it only records physical distance "
    "from coworkers during the pandemic, not whether someone feels alone or "
    "lacks social contact — a different idea entirely."
)


async def run_compose(responses, variables=(RJWRKCLSSD_A,), question="Why doesn't that one count?"):
    gateway = FakeLLMGateway(responses=list(responses))
    text = await cite_explain.compose(
        question=question,
        prior_question=PRIOR_QUESTION,
        prior_answer=PRIOR_ANSWER,
        variables=list(variables),
        gateway=gateway,
    )
    return text, gateway


# --- metadata shortcut: no LLM call at all -----------------------------------
def test_page_question_is_answered_without_an_llm_call():
    text = cite_explain.metadata_answer(CITATIONS, "What page is that on?")
    assert text == "RJWRKCLSSD_A is on page 576 (2020)."


def test_year_question_is_answered_without_an_llm_call():
    text = cite_explain.metadata_answer(CITATIONS, "What year is that from?")
    assert text == "RJWRKCLSSD_A is from the 2020 codebook."


def test_section_question_is_answered_without_an_llm_call():
    text = cite_explain.metadata_answer(CITATIONS, "What section is that in?")
    assert text == "RJWRKCLSSD_A is in the Social distancing at work section."


def test_a_reasoning_question_is_not_a_metadata_shortcut():
    assert cite_explain.metadata_answer(CITATIONS, "Why doesn't that one count?") is None


def test_no_citations_means_no_shortcut():
    assert cite_explain.metadata_answer([], "What page is that on?") is None


# --- grounding: the variable-drift regression --------------------------------
def test_ungrounded_variables_catches_a_name_not_in_the_cited_set():
    """The exact failure: explaining RJCLSSD1_A when RJWRKCLSSD_A was cited."""
    text = "RJCLSSD1_A does not measure loneliness because it is about distance."
    assert cite_explain.ungrounded_variables(text, {"RJWRKCLSSD_A"}) == ["RJCLSSD1_A"]


def test_a_reply_naming_only_the_cited_variable_is_grounded():
    assert cite_explain.ungrounded_variables(GOOD, {"RJWRKCLSSD_A"}) == []


async def test_a_grounded_reply_is_returned_as_is():
    text, gateway = await run_compose([GOOD])
    assert text == GOOD
    assert len(gateway.calls) == 1


async def test_a_reply_naming_the_wrong_variable_is_retried_with_the_name_flagged():
    """Reproduces the RJWRKCLSSD_A -> RJCLSSD1_A drift and confirms the fix."""
    drifted = (
        "RJCLSSD1_A does not measure loneliness because it only records "
        "physical distance at work, not feelings of isolation."
    )
    text, gateway = await run_compose([drifted, GOOD])
    assert text == GOOD
    assert len(gateway.calls) == 2
    assert "RJCLSSD1_A" in gateway.calls[1]["messages"][0].content
    # The model given a second chance never again sees the wrong variable as
    # an option, because `variables` only ever contained RJWRKCLSSD_A.
    assert "RJCLSSD1_A" not in text


async def test_a_reply_that_keeps_naming_the_wrong_variable_falls_back_to_the_prior_answer():
    drifted = "RJCLSSD1_A is a different, unrelated item."
    text, _ = await run_compose([drifted, drifted])
    assert text.startswith(PRIOR_ANSWER)
    assert "RJCLSSD1_A" not in text


async def test_an_opinion_about_the_cited_variables_is_allowed():
    """'Which one is most used' needs a judgement call, not a coverage claim."""
    opinion = (
        "FDSCAT4_A is the one most researchers use, because it is the official "
        "four-category recode rather than a single item."
    )
    text, gateway = await run_compose(
        [opinion],
        variables=[
            Hit(
                variable_name="FDSCAT4_A",
                survey_year=2023,
                page=531,
                similarity=0.6,
                description="Adult 4-category food security recode",
            ),
            Hit(
                variable_name="FDSCAT3_A",
                survey_year=2023,
                page=530,
                similarity=0.5,
                description="Adult 3-category food security recode",
            ),
        ],
        question="Which of those is the one most researchers use?",
    )
    assert text == opinion
    assert len(gateway.calls) == 1
