"""Weighted statistics tests (Requirement 4.2), including Property 4.

Property 4 validates `weighted_mean` against an independent reference,
`numpy.average`, exactly as design.md specifies.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from services.api.analysis.descriptive import (
    WeightingError,
    weighted_mean,
    weighted_proportions,
)


def test_weighted_mean_basic():
    # (1*10 + 3*30) / (10 + 30) = 100/40 = 2.5
    assert weighted_mean([1, 3], [10, 30]) == pytest.approx(2.5)


def test_weighted_mean_equals_simple_mean_with_equal_weights():
    assert weighted_mean([2, 4, 6], [1, 1, 1]) == pytest.approx(4.0)


def test_weighted_mean_ignores_zero_weight_rows():
    assert weighted_mean([10, 20, 1000], [5, 5, 0]) == pytest.approx(15.0)


def test_weighted_mean_rejects_mismatched_lengths():
    with pytest.raises(WeightingError):
        weighted_mean([1, 2], [1])


def test_weighted_mean_rejects_all_zero_weights():
    with pytest.raises(WeightingError):
        weighted_mean([1, 2, 3], [0, 0, 0])


def test_weighted_mean_rejects_empty():
    with pytest.raises(WeightingError):
        weighted_mean([], [])


def test_weighted_proportions_sum_to_one():
    props = weighted_proportions(["a", "b", "a"], [10, 10, 10])
    assert sum(p.proportion for p in props) == pytest.approx(1.0)
    by_cat = {p.category: p.proportion for p in props}
    assert by_cat["a"] == pytest.approx(2 / 3)
    assert by_cat["b"] == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# Property 4: Weighted statistic correctness
# ---------------------------------------------------------------------------
@given(
    pairs=st.lists(
        st.tuples(
            st.floats(
                min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
            ),
            st.floats(
                min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False
            ),
        ),
        min_size=1,
        max_size=50,
    )
)
def test_property_4_weighted_mean_matches_numpy(pairs):
    """Feature: differ-platform, Property 4: Weighted statistic correctness.

    The weighted mean must equal numpy.average with the same weights, for any
    set of values and positive weights.
    """
    values = [p[0] for p in pairs]
    weights = [p[1] for p in pairs]

    expected = float(np.average(values, weights=weights))
    assert weighted_mean(values, weights) == pytest.approx(expected, rel=1e-9, abs=1e-6)
