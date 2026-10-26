"""Sentinel classification tests (Requirements 5.1, 5.2, 5.3).

SAFETY-CRITICAL. Includes the HISPALLP_A regression fixture required by
Task 4.4 and the independence property (design.md Property 7).

These exist because the sentinel logic has already regressed twice: a
normalization change turned "Don't Know" into "don t know", which silently
reclassified roughly 590 missing-data codes in the 2023 adult file as
substantive answers. The failure was invisible in behaviour — every endpoint
still returned a confident answer. `test_dont_know_variants_are_sentinels`
is the guard, and it must be committed in the same change as any edit to
sentinels.py, not after.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from jobs.ingest.sentinels import (
    SENTINEL_PHRASES,
    compute_is_sentinel,
    is_range_of_values,
    normalize_label,
)

# ---------------------------------------------------------------------------
# Task 4.4 — hard-coded HISPALLP_A regression fixture.
#
# Transcribed from the real 2023 NHIS adult codebook, printed page 21. A
# literal table rather than generated data, so it also catches an
# implementation that satisfies the general property while still special-casing
# a "7/8/9 is always sentinel" shortcut.
# ---------------------------------------------------------------------------
HISPALLP_A_2023: list[tuple[int, str, bool]] = [
    (1, "Hispanic", False),
    (2, "Non-Hispanic White only", False),
    (3, "Non-Hispanic Black/African American only", False),
    (4, "Non-Hispanic Asian only", False),
    (5, "Non-Hispanic AIAN only", False),
    (6, "Non-Hispanic AIAN and any other group", False),
    (7, "Other single and multiple races", False),  # <- a REAL racial category
    (97, "Refused", True),
    (98, "Not Ascertained", True),
    (99, "Don't Know", True),
]


@pytest.mark.parametrize(("code", "label", "expected"), HISPALLP_A_2023)
def test_hispallp_a_sentinel_classification(code: int, label: str, expected: bool):
    assert compute_is_sentinel(Decimal(code), label) is expected


def test_hispallp_a_code_7_is_never_a_sentinel():
    """The single most consequential assertion in the ingestion pipeline.

    A blanket "exclude 7, 8, 9" rule would silently drop the 316 respondents
    who reported "Other single and multiple races" from every analysis.
    """
    assert compute_is_sentinel(Decimal(7), "Other single and multiple races") is False


def test_code_7_can_be_a_sentinel_on_a_different_variable():
    """Same number, opposite verdict — decided purely by that row's label."""
    assert compute_is_sentinel(Decimal(7), "Refused") is True


@pytest.mark.parametrize(
    "label",
    ["Don't Know", "Don't know", "DON'T KNOW", "Don\u2019t Know", "dont know"],
)
def test_dont_know_variants_are_sentinels(label: str):
    """Regression guard: apostrophes must fold away, not split the word.

    If `normalize_label` replaces the apostrophe with a space, "Don't Know"
    becomes "don t know", misses the phrase list, and ~590 missing-data codes
    get counted as real answers. This has happened twice already.
    """
    assert normalize_label(label) == "dont know"
    assert compute_is_sentinel(Decimal(99), label) is True


# ---------------------------------------------------------------------------
# Labels observed across the real 2023 codebook.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label",
    [
        "Refused",
        "Not Ascertained",
        "Not ascertained",
        "Don't Know",
        "Don't know",
        "Unknown",
        "Not available",
    ],
)
def test_observed_sentinel_labels(label: str):
    assert compute_is_sentinel(Decimal(9), label) is True


@pytest.mark.parametrize(
    "label",
    [
        "85+ years",
        "$40,000 or more",
        "10+ days",
        "4 or more cancers",
        "No cancer",
        "None",
        "Did not work",
        "Yes",
        "No",
        "Hispanic",
        "Race unknown to respondent",  # contains 'unknown' but is substantive
    ],
)
def test_substantive_labels_are_not_sentinels(label: str):
    assert compute_is_sentinel(Decimal(1), label) is False


def test_bundled_sentinel_label():
    assert compute_is_sentinel(Decimal(9), "Don't know/Refused") is True
    assert compute_is_sentinel(Decimal(9), "Refused, Not Ascertained") is True


def test_bundled_label_with_one_substantive_part_is_not_sentinel():
    """Erring toward substantive: dropping a real category is the worse error."""
    assert compute_is_sentinel(Decimal(9), "Hispanic/Refused") is False


def test_fractional_sentinel_code():
    """Continuous variables use fractional sentinels, e.g. BMI 99.9 = Unknown."""
    assert compute_is_sentinel(Decimal("99.9"), "Unknown") is True


def test_empty_label_is_not_a_sentinel():
    assert compute_is_sentinel(Decimal(1), "") is False
    assert compute_is_sentinel(Decimal(1), "   ") is False


# ---------------------------------------------------------------------------
# Property 7: Sentinel computation independence
# ---------------------------------------------------------------------------
_LABELS = st.sampled_from(
    sorted(SENTINEL_PHRASES) + ["Hispanic", "Yes", "85+ years", "No cancer"]
)


@given(
    code=st.integers(min_value=0, max_value=99999),
    own_label=_LABELS,
    other_label=_LABELS,
)
def test_property_7_sentinel_computation_independence(
    code: int, own_label: str, other_label: str
):
    """Feature: differ-platform, Property 7: Sentinel computation independence.

    For all pairs of distinct variables that happen to share a numeric code
    value, computing is_sentinel for one variable's code must be unaffected by
    whatever is_sentinel value the other variable has for the same numeric
    code.
    """
    verdict_alone = compute_is_sentinel(Decimal(code), own_label)
    compute_is_sentinel(Decimal(code), other_label)
    verdict_after = compute_is_sentinel(Decimal(code), own_label)

    assert verdict_alone == verdict_after


@given(
    code_a=st.integers(min_value=0, max_value=99999),
    code_b=st.integers(min_value=0, max_value=99999),
    label=_LABELS,
)
def test_property_7_verdict_ignores_the_numeric_code(
    code_a: int, code_b: int, label: str
):
    """The same label must classify identically regardless of its code.

    This is the structural reason no global "codes 7/8/9" rule can creep in.
    """
    assert compute_is_sentinel(Decimal(code_a), label) == compute_is_sentinel(
        Decimal(code_b), label
    )


# ---------------------------------------------------------------------------
# Range rows
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Range of values", True),
        # The codebook trails frequency and percent columns after the phrase,
        # so this must be a prefix match, not an exact one.
        ("Range of values                 29522      100.00", True),
        ("range of values", True),
        ("Hispanic", False),
        ("", False),
    ],
)
def test_is_range_of_values(label: str, expected: bool):
    assert is_range_of_values(label) is expected
