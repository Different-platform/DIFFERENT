"""Weighted descriptive statistics (Requirement 4.2).

NHIS is a complex sample survey: every respondent carries a final annual weight
(`WTFA_A`), and an unweighted mean of the microdata does not estimate anything
about the US population. The weight is not optional decoration — ignoring it
produces a number that is simply wrong for the question a researcher is asking.

In the current path the generated SQL performs the weighted aggregation in
Postgres (SUM(x*w)/SUM(w) over all rows), which is both correct over the full
30k rows and cheap. This module provides the same statistics in Python for the
cases that return row-level data, and — importantly — it is the independent
reference the property test checks the whole idea against (Property 4 compares
`weighted_mean` to `numpy.average`).

Design variables for proper variance estimation (`PSTRAT`, `PPSU`) and
clustered standard errors are a later phase (statsmodels); the point estimates
here are correct and are what the UI shows now.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


class WeightingError(ValueError):
    """Raised when inputs cannot yield a valid weighted estimate."""


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    """Population weighted mean: sum(w*x) / sum(w).

    Rows whose weight is non-positive (or missing) contribute nothing — a
    zero-weight respondent is out of scope for the estimate, not an error.
    """
    v = np.asarray(values, dtype="float64")
    w = np.asarray(weights, dtype="float64")
    if v.shape != w.shape:
        raise WeightingError("values and weights must have the same length")
    if v.size == 0:
        raise WeightingError("no rows to average")

    mask = np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not mask.any():
        raise WeightingError("no rows with a positive weight")

    total = float(w[mask].sum())
    if total <= 0:
        raise WeightingError("total weight is not positive")
    return float((v[mask] * w[mask]).sum() / total)


@dataclass(frozen=True)
class WeightedProportion:
    category: object
    weighted_count: float
    proportion: float


def weighted_proportions(
    categories: Sequence[object], weights: Sequence[float]
) -> list[WeightedProportion]:
    """Weighted share of the population in each category.

    The proportions sum to 1 (within floating-point tolerance) across the
    categories present, so a crosstab reads as a population distribution rather
    than a raw respondent count.
    """
    cats = list(categories)
    w = np.asarray(weights, dtype="float64")
    if len(cats) != w.size:
        raise WeightingError("categories and weights must have the same length")
    if w.size == 0:
        raise WeightingError("no rows to tabulate")

    totals: dict[object, float] = {}
    grand = 0.0
    for category, weight in zip(cats, w, strict=True):
        if not np.isfinite(weight) or weight <= 0:
            continue
        totals[category] = totals.get(category, 0.0) + float(weight)
        grand += float(weight)

    if grand <= 0:
        raise WeightingError("no rows with a positive weight")

    return [
        WeightedProportion(
            category=category,
            weighted_count=weighted_total,
            proportion=weighted_total / grand,
        )
        for category, weighted_total in sorted(
            totals.items(), key=lambda item: str(item[0])
        )
    ]
