"""Which survey year a question is about (rag/scope.py)."""

from __future__ import annotations

import pytest

from services.api.rag.scope import DEFAULT_STATS_YEAR, infer_year, resolve_year


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is the average age in 2021?", 2021),
        ("BMI for adults in 2023", 2023),
        ("compare 2019 and 2023", None),  # two years: ambiguous, infer nothing
        ("average age of adults", None),
        ("since 1999", None),  # outside the covered range
        ("2019 2019", 2019),  # the same year twice is still one year
    ],
)
def test_infer_year(text, expected):
    assert infer_year(text) == expected


def test_year_in_question_beats_selector():
    assert resolve_year("average age in 2021", 2023, "data") == (2021, False)


def test_selector_used_when_question_names_no_year():
    assert resolve_year("average age", 2020, "data") == (2020, False)


def test_statistics_question_defaults_to_latest_year_and_says_so():
    assert resolve_year("average age", None, "data") == (DEFAULT_STATS_YEAR, True)
    assert resolve_year("average age", None, "both") == (DEFAULT_STATS_YEAR, True)


def test_codebook_question_stays_cross_year():
    assert resolve_year("how is food insecurity measured", None, "codebook") == (
        None,
        False,
    )
