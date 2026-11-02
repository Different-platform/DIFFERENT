"""Research guidance about a computed answer (rag/guidance.py)."""

from __future__ import annotations

from decimal import Decimal

from services.api.domain import Hit, ValueLabel
from services.api.llm.gateway import FakeLLMGateway
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
SUMMARY = "The population-weighted mean for U.S. adults in 2022 is 27.96."
PRIOR_QUESTION = "What is the weighted mean BMI for adults in 2022?"
MESSAGE = "How can I use this BMI in my research?"


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
    "The 27.96 figure works as background for describing U.S. adults in 2022.\n\n"
    "It has no standard error, so re-estimate it with PSTRAT and PPSU before "
    "testing differences. BMI was derived from self-reported height and weight."
)


async def run_compose(responses, sql=BMI_SQL, variables=(HEIGHT, WEIGHT)):
    gateway = FakeLLMGateway(responses=list(responses))
    text = await guidance.compose(
        message=MESSAGE,
        prior_question=PRIOR_QUESTION,
        summary=SUMMARY,
        sql=sql,
        variables=list(variables),
        gateway=gateway,
    )
    return text, gateway


def joined(notes):
    return " ".join(notes)


# --- research notes -----------------------------------------------------------
def test_notes_name_the_weight_year_and_missing_standard_error():
    notes = joined(guidance.research_notes(sql=BMI_SQL, variables=[HEIGHT, WEIGHT]))
    assert "WTFA_A" in notes and "2022" in notes
    assert "No standard error" in notes and "PSTRAT" in notes and "PPSU" in notes


def test_notes_say_bmi_was_derived_from_self_report_when_the_sql_derived_it():
    notes = joined(guidance.research_notes(sql=BMI_SQL, variables=[]))
    assert "self-reported height and weight" in notes
    assert "not a variable NHIS publishes" in notes


def test_notes_do_not_claim_a_derivation_for_a_published_recode():
    """BMICAT_A is published by NHIS; saying we derived it would be false."""
    notes = joined(guidance.research_notes(sql=OBESE_SQL, variables=[]))
    assert "not a variable NHIS publishes" not in notes


def test_notes_list_the_missing_data_labels_once_each():
    notes = joined(guidance.research_notes(sql=BMI_SQL, variables=[HEIGHT, WEIGHT]))
    assert "(Not available, Refused, Don't Know)" in notes


def test_notes_cite_the_year_and_the_variables_used():
    notes = joined(guidance.research_notes(sql=BMI_SQL, variables=[]))
    assert "National Health Interview Survey, 2022 Sample Adult" in notes
    assert "WEIGHTLBTC_A, HEIGHTTC_A" in notes


def test_notes_without_a_survey_table_still_make_sense():
    notes = joined(guidance.research_notes(sql="SELECT 1", variables=[]))
    assert "None" not in notes
    assert "National Health Interview Survey, Sample Adult" in notes


def test_notes_contain_no_number_the_material_cannot_ground():
    """The fallback is the notes, so they must pass the same number check."""
    notes = guidance.research_notes(sql=BMI_SQL, variables=[HEIGHT, WEIGHT])
    source = "\n".join([BMI_SQL, *notes])
    assert explain.ungrounded_numbers(guidance.fallback(notes), source) == []


# --- composing the reply ------------------------------------------------------
async def test_a_grounded_reply_is_returned_as_is():
    text, gateway = await run_compose([GOOD])
    assert text == GOOD
    assert len(gateway.calls) == 1
    prompt = gateway.calls[0]["messages"][0].content
    assert MESSAGE in prompt and "Research notes" in prompt and BMI_SQL in prompt
    assert gateway.calls[0]["stage"] == "guidance"


async def test_an_invented_figure_is_retried_with_the_offending_number_named():
    invented = GOOD + " Adults with a BMI over 30 are obese, about 33.8%."
    text, gateway = await run_compose([invented, GOOD])
    assert text == GOOD
    assert len(gateway.calls) == 2
    assert "33.8" in gateway.calls[1]["messages"][0].content


async def test_a_reply_that_keeps_inventing_falls_back_to_the_notes():
    invented = GOOD + " About 26,969 respondents contributed."
    text, _ = await run_compose([invented, invented])
    assert "26,969" not in text
    assert text.startswith("Here is how to use this figure in research.")
    assert "No standard error" in text


async def test_an_empty_reply_falls_back_to_the_notes():
    text, _ = await run_compose(["", ""])
    assert text.startswith("Here is how to use this figure in research.")


def test_notes_do_not_call_2019_to_2023_incomparable():
    """The first live reply said 2022 was not comparable with 'earlier years';
    2019-2023 share one questionnaire, only pre-2019 is cut off."""
    notes = joined(guidance.research_notes(sql=BMI_SQL, variables=[]))
    assert "2019 to 2023 all use the questionnaire" in notes
    assert "2018 or earlier are not directly comparable" in notes


async def test_look_alike_spaces_and_hyphens_are_made_plain():
    """gpt-oss writes U+202F and U+2011, which break copy-paste and search."""
    styled = "Use the R\u202fsurvey package; BMI is self\u2011reported."
    text, _ = await run_compose([styled])
    assert text == "Use the R survey package; BMI is self-reported."
