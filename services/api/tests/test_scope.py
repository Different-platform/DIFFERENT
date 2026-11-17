"""Which survey year a question is about (rag/scope.py)."""

from __future__ import annotations

import pytest

from services.api.rag.scope import (
    DEFAULT_STATS_YEAR,
    infer_year,
    infer_years,
    resolve_year,
)


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


# --- infer_years: detecting a cross-year comparison question ---------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is the average age in 2021?", [2021]),
        ("compare 2019 and 2023", [2019, 2023]),
        ("2017-2019 versus 2023-2025", [2017, 2018, 2019, 2023, 2024, 2025]),
        ("2017 to 2019", [2017, 2018, 2019]),
        ("2017\u20132019", [2017, 2018, 2019]),  # en dash
        ("average age of adults", []),
        ("1000-2000 participants were surveyed", []),  # span too wide, not years
        ("2019 2019", [2019]),  # repeated mention, counted once
    ],
)
def test_infer_years(text, expected):
    assert infer_years(text) == expected


def test_infer_years_does_not_double_count_a_year_inside_a_range():
    # "2019" appears both as the range's start and would otherwise also match
    # the bare-year pattern; it must be counted once.
    assert infer_years("from 2019-2021, was there a change since 2019?") == [
        2019, 2020, 2021,
    ]


def test_infer_years_includes_years_nhis_has_not_released():
    """2024/2025 are not valid NHIS years at all, but must still be surfaced
    so the caller can explain their absence rather than silently dropping
    the researcher's actual request."""
    years = infer_years("comparing 2023 to 2025")
    assert 2025 in years
