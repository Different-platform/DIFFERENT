"""Answers about numbers already shown (rag/guidance.py)."""

from __future__ import annotations

from decimal import Decimal

from services.api.domain import Hit, ValueLabel
from services.api.llm.gateway import FakeLLMGateway, Message
from services.api.rag import explain, guidance

BMI_SQL = (
    "SELECT SUM(703.0 * WEIGHTLBTC_A / (HEIGHTTC_A * HEIGHTTC_A) * WTFA_A) / "
    "SUM(WTFA_A) AS weighted_mean_bmi FROM survey_2022 WHERE HEIGHTTC_A NOT IN "
    "(96, 97, 98, 99) AND WEIGHTLBTC_A NOT IN (996, 997, 998, 999)"
)
OBESE_SQL = (
    "SELECT SUM(CASE WHEN BMICAT_A = 4 THEN WTFA_A ELSE 0 END) / SUM(WTFA_A) "
    "AS share FROM survey_2023 WHERE BMICAT_A NOT IN (9)"
)
SUMMARY = "The average BMI for U.S. adults in 2022 was 27.96."
PRIOR_QUESTION = "What is the weighted mean BMI for adults in 2022?"
MESSAGE = "How can I use this BMI in my research?"

#: The conversation from the screenshot: 2023, then 2022, then "why?".
EARLIER = [
    Message(role="user", content="What's the weighted mean BMI for adults in 2023?"),
    Message(role="assistant", content="The average BMI for U.S. adults in 2023 was 28.03."),
    Message(role="user", content="BMI for 2022?"),
    Message(role="assistant", content=SUMMARY),
]


def make_hit(name, labels):
    return Hit(
        variable_name=name,
        survey_year=2022,
        page=1,
        similarity=0.5,
        description=name,
        value_labels=[
            ValueLabel(code=Decimal(code), label=label, is_sentinel=True)
            for code, label in labels
        ],
    )


HEIGHT = make_hit("HEIGHTTC_A", [(96, "Not available"), (97, "Refused")])
WEIGHT = make_hit("WEIGHTLBTC_A", [(996, "Not available"), (999, "Don't Know")])

GOOD = (
    "You can use 27.96 to describe the typical BMI of U.S. adults in 2022.\n\n"
    "It cannot show why BMI changes. For a paper: re-run it with PSTRAT and "
    "PPSU to get a margin of error."
)


async def run_compose(
    responses, sql=BMI_SQL, variables=(HEIGHT, WEIGHT), message=MESSAGE, earlier=()
):
    gateway = FakeLLMGateway(responses=list(responses))
    text = await guidance.compose(
        message=message,
        prior_question=PRIOR_QUESTION,
        summary=SUMMARY,
        sql=sql,
        variables=list(variables),
        gateway=gateway,
        earlier=list(earlier),
    )
    return text, gateway


def joined(notes):
    return " ".join(notes)


# --- notes ---------------------------------------------------------------------
def test_notes_lead_with_what_the_survey_cannot_tell_you():
    notes = guidance.research_notes(sql=BMI_SQL, variables=[])
    assert "does not record the reasons" in notes[0]
    assert "can happen by chance" in notes[1]
    assert "No margin of error was calculated" in notes[1]


def test_notes_say_bmi_came_from_self_reported_height_and_weight():
    notes = joined(guidance.research_notes(sql=BMI_SQL, variables=[]))
    assert "height and weight people reported themselves" in notes
    assert "usually comes out lower" in notes


def test_notes_do_not_claim_a_calculation_for_a_published_recode():
    """BMICAT_A is published by NHIS; saying we worked it out would be false."""
    notes = joined(guidance.research_notes(sql=OBESE_SQL, variables=[]))
    assert "people reported themselves" not in notes


def test_notes_list_the_missing_answers_once_each_in_lower_case():
    notes = joined(guidance.research_notes(sql=BMI_SQL, variables=[HEIGHT, WEIGHT]))
    assert "(not available, refused, don't know)" in notes


def test_the_technical_note_is_last_and_names_year_variables_and_design():
    notes = guidance.research_notes(sql=BMI_SQL, variables=[])
    last = notes[-1]
    assert last.startswith("For a paper:")
    assert "National Health Interview Survey 2022 Sample Adult" in last
    assert "WEIGHTLBTC_A, HEIGHTTC_A" in last
    assert "PSTRAT" in last and "PPSU" in last and "WTFA_A" in last
    # Variable codes and design jargon stay out of every other note.
    for note in notes[:-1]:
        assert "PSTRAT" not in note and "_A" not in note


def test_notes_without_a_survey_table_still_make_sense():
    notes = joined(guidance.research_notes(sql="SELECT 1", variables=[]))
    assert "None" not in notes
    assert "National Health Interview Survey Sample Adult" in notes


def test_notes_do_not_call_2019_to_2023_incomparable():
    notes = joined(guidance.research_notes(sql=BMI_SQL, variables=[]))
    assert "2019 to 2023 use the same questionnaire" in notes
    assert "before 2019 used an older questionnaire" in notes


def test_fallback_is_short_and_contains_no_ungrounded_number():
    notes = guidance.research_notes(sql=BMI_SQL, variables=[HEIGHT, WEIGHT])
    text = guidance.fallback(notes)
    assert text.count("\n\n") == 4  # intro + three notes + the note for papers
    assert text.rstrip().endswith(notes[-1])
    source = "\n".join([BMI_SQL, *notes])
    assert explain.ungrounded_numbers(text, source) == []


# --- composing the reply -------------------------------------------------------
async def test_a_grounded_reply_is_returned_as_is():
    text, gateway = await run_compose([GOOD])
    assert text == GOOD
    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    prompt = call["messages"][0].content
    assert MESSAGE in prompt and "Notes (true for these numbers)" in prompt
    assert BMI_SQL in prompt
    assert call["stage"] == "guidance"
    # Plain-language rules travel with every guidance prompt.
    assert "no statistics, survey, or programming" in call["system"]


async def test_earlier_numbers_are_given_to_the_model_and_count_as_grounded():
    """'Why did BMI go up from 2022 to 2023?' needs both years' figures."""
    why = "What is the reason for the increase in BMI from 2022 to 2023?"
    reply = (
        "The survey cannot tell us why. It shows that the average BMI was 27.96 "
        "in 2022 and 28.03 in 2023, which is only slightly higher.\n\nA small "
        "difference like this can happen by chance."
    )
    text, gateway = await run_compose([reply], message=why, earlier=EARLIER)
    assert text == reply  # 28.03 came from the earlier turn, so it is grounded
    prompt = gateway.calls[0]["messages"][0].content
    assert "28.03" in prompt and "27.96" in prompt


async def test_a_calculated_difference_is_retried_and_named():
    """The model must describe a change in words, not compute a new number."""
    why = "Why did BMI go up from 2022 to 2023?"
    computed = "BMI rose by 0.07 points, from 27.96 to 28.03."
    fixed = "BMI was slightly higher in 2023 (28.03) than in 2022 (27.96)."
    text, gateway = await run_compose([computed, fixed], message=why, earlier=EARLIER)
    assert text == fixed
    assert "0.07" in gateway.calls[1]["messages"][0].content


async def test_a_reply_that_keeps_inventing_falls_back_to_the_notes():
    invented = GOOD + " About 26,969 respondents contributed."
    text, _ = await run_compose([invented, invented])
    assert "26,969" not in text
    assert text.startswith("Here is what this number can and cannot tell you.")


async def test_an_empty_reply_falls_back_to_the_notes():
    text, _ = await run_compose(["", ""])
    assert text.startswith("Here is what this number can and cannot tell you.")


async def test_look_alike_spaces_and_hyphens_are_made_plain():
    """gpt-oss writes U+202F and U+2011, which break copy-paste and search."""
    styled = "Use the R\u202fsurvey package; BMI is self\u2011reported."
    text, _ = await run_compose([styled])
    assert text == "Use the R survey package; BMI is self-reported."
