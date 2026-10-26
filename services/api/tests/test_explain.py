"""Explaining a computed answer (rag/explain.py, rag/followup.py)."""

from __future__ import annotations

import pytest

from services.api.llm.gateway import FakeLLMGateway
from services.api.rag import explain, followup

SQL = (
    "SELECT SUM(703.0 * WEIGHTLBTC_A / (HEIGHTTC_A * HEIGHTTC_A) * WTFA_A) / "
    "SUM(WTFA_A) AS weighted_mean_bmi FROM survey_2023 WHERE HEIGHTTC_A NOT IN "
    "(96, 97, 98, 99) AND WEIGHTLBTC_A NOT IN (996, 997, 998, 999)"
)
SUMMARY = "The population-weighted mean for U.S. adults in 2023 is 28.03."
QUESTION = "What is the weighted mean BMI for adults in 2023?"

GOOD = (
    "The figure 28.03 is a weighted mean BMI for 2023. BMI is 703 times weight "
    "in pounds divided by height in inches squared. Respondents coded 96, 97, 98 "
    "or 99 for height, or 996 to 999 for weight, were left out."
)


async def run_compose(responses):
    gateway = FakeLLMGateway(responses=list(responses))
    text = await explain.compose(
        question=QUESTION, summary=SUMMARY, sql=SQL, variables=[], gateway=gateway
    )
    return text, gateway


def test_year_and_variables_are_read_from_the_sql():
    assert explain.year_of(SQL) == 2023
    assert explain.variable_names(SQL) == ["WEIGHTLBTC_A", "HEIGHTTC_A"]


def test_year_of_a_query_with_no_table_is_none():
    assert explain.year_of("SELECT 1") is None


def test_numbers_from_the_material_are_grounded():
    assert explain.ungrounded_numbers(GOOD, SQL + SUMMARY) == []


def test_a_number_that_was_never_computed_is_caught():
    text = "About 26,969 respondents had valid data, and 41.2% were affected."
    assert explain.ungrounded_numbers(text, SQL + SUMMARY) == ["26,969", "41.2"]


def test_small_counts_and_survey_years_are_allowed():
    text = "There are two steps and four categories, measured in 2021."
    assert explain.ungrounded_numbers(text, "") == []


async def test_a_grounded_explanation_is_returned_as_is():
    text, gateway = await run_compose([GOOD])
    assert text == GOOD
    assert len(gateway.calls) == 1


async def test_an_invented_figure_is_retried_with_the_offending_number_named():
    invented = GOOD + " About 26,969 respondents contributed."
    text, gateway = await run_compose([invented, GOOD])
    assert text == GOOD
    assert len(gateway.calls) == 2
    assert "26,969" in gateway.calls[1]["messages"][0].content


async def test_explanation_that_keeps_inventing_falls_back_to_the_shown_summary():
    invented = GOOD + " About 26,969 respondents contributed."
    text, _ = await run_compose([invented, invented])
    assert text.startswith(SUMMARY)
    assert "26,969" not in text


# --- follow-up classification ------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"explain": true}', True),
        ('{"explain": false}', False),
        ('```json\n{"explain": true}\n```', True),
        ("not json at all", False),
        ('{"explain": "yes"}', False),  # only a real true counts
        ("", False),
    ],
)
def test_parse(raw, expected):
    assert followup._parse(raw) is expected


async def test_classifier_failure_means_use_the_normal_pipeline():
    from services.api.llm.gateway import LLMGatewayError

    class Broken:
        async def complete(self, **kwargs):
            raise LLMGatewayError("down")

    assert (
        await followup.asks_for_explanation(
            "Explain me in detail", QUESTION, SUMMARY, gateway=Broken()
        )
        is False
    )
