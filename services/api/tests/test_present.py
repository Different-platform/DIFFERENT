"""Turning a verified result into something a researcher can use."""

from __future__ import annotations

from decimal import Decimal

import re

import pytest

from services.api.domain import Hit, ValueLabel
from services.api.rag.present import is_empty, present_result, variables_in

#: Wording a reader without a statistics background should never meet.
_JARGON = re.compile(
    r"population-weighted|weighted mean|derived|sentinel|universe|"
    r"Excluded as missing|\b[A-Z][A-Z0-9]+_[A-Z]+\b",
)


def assert_plain(summary: str) -> None:
    """No statistics jargon and no variable codes in the prose."""
    found = _JARGON.findall(summary)
    assert not found, f"jargon in summary: {found}"


def make_hit(name, description, labels, universe=None):
    return Hit(
        variable_name=name,
        survey_year=2023,
        page=1,
        similarity=0.5,
        description=description,
        universe_description=universe,
        value_labels=[
            ValueLabel(code=Decimal(code), label=label, is_sentinel=sentinel)
            for code, label, sentinel in labels
        ],
    )


BMICAT = make_hit(
    "BMICAT_A",
    "Categorical Body Mass Index",
    [
        (1, "Underweight", False),
        (2, "Healthy weight", False),
        (3, "Overweight", False),
        (4, "Obese", False),
        (9, "Unknown", True),
    ],
)
HEIGHT = make_hit(
    "HEIGHTTC_A",
    "Height without shoes (inches)",
    [(96, "Not available", True), (97, "Refused", True), (99, "Don't Know", True)],
)
WEIGHT = make_hit(
    "WEIGHTLBTC_A",
    "Weight without shoes (pounds)",
    [(996, "Not available", True), (997, "Refused", True)],
)

DISTRIBUTION_SQL = (
    "SELECT BMICAT_A AS category, SUM(WTFA_A) / (SELECT SUM(WTFA_A) FROM "
    "survey_2023 WHERE BMICAT_A NOT IN (9)) AS proportion FROM survey_2023 "
    "WHERE BMICAT_A NOT IN (9) GROUP BY BMICAT_A ORDER BY BMICAT_A"
)


def test_distribution_codes_become_labels_and_fractions_become_percentages():
    presented = present_result(
        ["category", "proportion"],
        [[1, 0.0161], [2, 0.3061], [3, 0.3395], [4, 0.3383]],
        sql=DISTRIBUTION_SQL,
        hits=[BMICAT],
        year=2023,
    )
    assert presented.columns == ["Categorical Body Mass Index", "Percent of U.S. adults"]
    assert presented.rows[0] == ["Underweight", "1.6%"]
    assert presented.rows[2] == ["Overweight", "34.0%"]
    # The reader gets the finding, the exclusions, and the weighting, in words.
    assert "Underweight 1.6%" in presented.summary
    assert "largest group is Overweight" in presented.summary
    assert "People with no usable answer (unknown) were left out." in presented.summary
    assert "represent all U.S. adults" in presented.summary
    assert_plain(presented.summary)


def test_derived_scalar_is_described_as_derived():
    sql = (
        "SELECT SUM(703.0 * WEIGHTLBTC_A / (HEIGHTTC_A * HEIGHTTC_A) * WTFA_A) "
        "/ SUM(WTFA_A) AS weighted_mean_bmi FROM survey_2023 "
        "WHERE HEIGHTTC_A NOT IN (96, 97, 99) AND WEIGHTLBTC_A NOT IN (996, 997)"
    )
    presented = present_result(
        ["weighted_mean_bmi"],
        [[Decimal("29.8731")]],
        sql=sql,
        hits=[WEIGHT, HEIGHT, BMICAT],
        year=2023,
    )
    assert presented.rows == [[29.87]]
    assert presented.columns == ["Average"]
    assert presented.summary.startswith("The average BMI for U.S. adults in 2023 was 29.87.")
    # Says what BMI is and where it came from, in words.
    assert "compares a person's weight with their height" in presented.summary
    assert "height and weight people reported" in presented.summary
    # And what the number means on the standard scale.
    assert "falls in the overweight range" in presented.summary
    assert "refused" in presented.summary  # what was excluded is stated
    assert_plain(presented.summary)


@pytest.mark.parametrize(
    ("value", "band"),
    [("18.4", "underweight"), ("18.5", "healthy weight"), ("24.99", "healthy weight"),
     ("25", "overweight"), ("29.99", "overweight"), ("30", "obese")],
)
def test_bmi_band_uses_the_standard_cut_offs(value, band):
    from services.api.rag.present import _bmi_band

    assert _bmi_band(Decimal(value)) == band


def test_a_derived_measure_other_than_bmi_gets_no_bmi_wording():
    other = make_hit("HHINC_A", "Household income", [])
    sql = (
        "SELECT SUM(HHINC_A / AGEP_A * WTFA_A) / SUM(WTFA_A) AS weighted_mean "
        "FROM survey_2023"
    )
    age = make_hit("AGEP_A", "Age of SA (top coded)", [])
    presented = present_result(
        ["weighted_mean"], [[3.2]], sql=sql, hits=[other, age], year=2023
    )
    assert "BMI" not in presented.summary
    assert "worked out from household income and age" in presented.summary


def test_single_variable_mean_is_not_called_derived():
    age = make_hit("AGEP_A", "Age of SA", [(97, "Refused", True)])
    presented = present_result(
        ["weighted_mean"],
        [[48.1009]],
        sql="SELECT SUM(AGEP_A * WTFA_A) / SUM(WTFA_A) AS weighted_mean "
        "FROM survey_2023 WHERE AGEP_A NOT IN (97)",
        hits=[age],
        year=2023,
    )
    assert "worked out from" not in presented.summary
    assert "The average age for U.S. adults in 2023 was 48.1" in presented.summary


def test_assumed_year_is_disclosed():
    presented = present_result(
        ["weighted_mean"],
        [[48.1]],
        sql="SELECT SUM(AGEP_A * WTFA_A) / SUM(WTFA_A) AS weighted_mean "
        "FROM survey_2023 WHERE AGEP_A NOT IN (97)",
        hits=[make_hit("AGEP_A", "Age", [(97, "Refused", True)])],
        year=2023,
        year_assumed=True,
    )
    assert "No survey year was selected" in presented.summary


def test_narrow_universe_is_called_out():
    narrow = make_hit(
        "DIBTYPE_A",
        "Diabetes type",
        [(1, "Type 1", False), (2, "Type 2", False), (9, "Unknown", True)],
        universe="Sample adults ever told they have diabetes",
    )
    presented = present_result(
        ["category", "proportion"],
        [[1, 0.1], [2, 0.9]],
        sql="SELECT DIBTYPE_A AS category, SUM(WTFA_A) / 1 AS proportion "
        "FROM survey_2023 WHERE DIBTYPE_A NOT IN (9) GROUP BY DIBTYPE_A",
        hits=[narrow],
        year=2023,
    )
    assert "ever told they have diabetes" in presented.summary


def test_every_number_in_the_rows_comes_from_the_result():
    """Presentation formats; it must never invent a figure."""
    raw = [[1, 0.25], [2, 0.75]]
    presented = present_result(
        ["category", "proportion"],
        raw,
        sql=DISTRIBUTION_SQL,
        hits=[BMICAT],
        year=2023,
    )
    shown = [row[1] for row in presented.rows]
    assert shown == ["25.0%", "75.0%"]


def test_is_empty():
    assert is_empty([])
    assert is_empty([[None]])
    assert not is_empty([[0]])  # zero is a real answer
    assert not is_empty([[48.1]])


def test_variables_in_ignores_plumbing_and_matches_whole_words():
    weight = make_hit("WTFA_A", "Weight - Final Annual", [])
    found = variables_in("SELECT BMICAT_A_EXTRA, WTFA_A FROM t", [BMICAT, weight])
    assert found == []  # BMICAT_A_EXTRA is not BMICAT_A; WTFA_A is plumbing


SEX = make_hit(
    "SEX_A",
    "Sex of Sample Adult",
    [(1, "Male", False), (2, "Female", False), (7, "Refused", True), (9, "Unknown", True)],
)

WITHIN_SQL = (
    "SELECT SEX_A, BMICAT_A, SUM(WTFA_A) / SUM(SUM(WTFA_A)) OVER (PARTITION BY SEX_A) "
    "AS proportion FROM survey_2023 WHERE SEX_A NOT IN (7, 9) AND BMICAT_A NOT IN (9) "
    "GROUP BY SEX_A, BMICAT_A ORDER BY SEX_A, BMICAT_A"
)


def test_two_way_table_labels_both_columns_and_says_within_group():
    """'Male 0.5%, Male 13.0%' was unreadable: every coded column must show."""
    presented = present_result(
        ["sex_a", "bmicat_a", "proportion"],
        [[1, 1, 0.02], [1, 4, 0.98], [2, 1, 0.04], [2, 4, 0.96]],
        sql=WITHIN_SQL,
        hits=[SEX, BMICAT],
        year=2023,
    )
    assert presented.columns[:2] == ["Sex", "Categorical Body Mass Index"]
    assert presented.rows[0] == ["Male", "Underweight", "2.0%"]
    assert "within each sex group" in presented.summary
    assert "Male: Underweight 2.0%, Obese 98.0%" in presented.summary
    assert "Female: Underweight 4.0%, Obese 96.0%" in presented.summary
    assert "within each group" in presented.summary


def test_joint_two_way_table_is_described_as_shares_of_everyone():
    sql = WITHIN_SQL.replace(
        "SUM(WTFA_A) / SUM(SUM(WTFA_A)) OVER (PARTITION BY SEX_A)",
        "SUM(WTFA_A) / 100",
    )
    presented = present_result(
        ["sex_a", "bmicat_a", "proportion"],
        [[1, 1, 0.02], [2, 4, 0.98]],
        sql=sql,
        hits=[SEX, BMICAT],
        year=2023,
    )
    assert "each combination of sex and categorical Body Mass Index" in presented.summary
    assert "Male, Underweight 2.0%" in presented.summary


SHARE_SQL = (
    "SELECT SUM(CASE WHEN BMICAT_A = 4 THEN WTFA_A ELSE 0 END) / SUM(WTFA_A) "
    "AS proportion FROM survey_2023 WHERE BMICAT_A NOT IN (9)"
)


def test_share_in_one_category_says_which_category():
    """'The share is 33.8%' left the reader asking: share of what?"""
    presented = present_result(
        ["proportion"], [[0.338]], sql=SHARE_SQL, hits=[BMICAT], year=2023
    )
    assert presented.rows == [["33.8%"]]
    assert "About 33.8% of U.S. adults in 2023" in presented.summary
    assert '"Obese"' in presented.summary
    assert_plain(presented.summary)


def test_category_share_by_group_is_not_called_a_distribution():
    sql = (
        "SELECT SEX_A, SUM(CASE WHEN BMICAT_A = 4 THEN WTFA_A ELSE 0 END) / "
        "SUM(WTFA_A) AS proportion FROM survey_2023 WHERE BMICAT_A NOT IN (9) "
        "AND SEX_A NOT IN (7, 9) GROUP BY SEX_A ORDER BY SEX_A"
    )
    presented = present_result(
        ["sex_a", "proportion"],
        [[1, 0.342], [2, 0.335]],
        sql=sql,
        hits=[SEX, BMICAT],
        year=2023,
    )
    assert presented.rows == [["Male", "34.2%"], ["Female", "33.5%"]]
    assert '"Obese"' in presented.summary
    assert "within each sex group" in presented.summary
    assert "do not add up to 100%" in presented.summary
    assert "Male 34.2%, Female 33.5%" in presented.summary
    # These do not sum to 100%, so announcing a "largest group" would mislead.
    assert "largest group" not in presented.summary
    assert "distribution of" not in presented.summary
