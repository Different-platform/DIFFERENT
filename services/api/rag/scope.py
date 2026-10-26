"""Decide which survey year a question is about.

Mixing years is the quiet failure here. With no year selected, retrieval spans
every ingested year, so a question about BMI cites BMICAT_A from 2022 next to
HEIGHTTC_A from 2019, and a statistic has no single table to run against.

Rules, in priority order:

1. A single year named in the question wins ("... in 2021"). It is the most
   specific statement of intent, and it beats a stale year-selector value.
   Two or more different years are ambiguous, so none is inferred.
2. Otherwise the year the researcher selected.
3. Otherwise, for a statistics question only, the most recent year, flagged so
   the answer can say it assumed it. A codebook question with no year stays
   cross-year, because "what does NHIS measure" legitimately spans years.
"""

from __future__ import annotations

import re

from services.api.domain import SURVEY_YEARS_MAX, RouteIntent

_YEAR = re.compile(r"\b(20(?:1[7-9]|2[0-3]))\b")

#: Year assumed for a statistics question that names none.
DEFAULT_STATS_YEAR = SURVEY_YEARS_MAX


def infer_year(text: str) -> int | None:
    """The single survey year named in `text`, or None if zero or several."""
    years = {int(match) for match in _YEAR.findall(text or "")}
    return next(iter(years)) if len(years) == 1 else None


def resolve_year(
    text: str, selected: int | None, intent: RouteIntent | None
) -> tuple[int | None, bool]:
    """Return (year, assumed). `assumed` is True only for the default fallback."""
    mentioned = infer_year(text)
    if mentioned is not None:
        return mentioned, False
    if selected is not None:
        return selected, False
    if intent in ("data", "both"):
        return DEFAULT_STATS_YEAR, True
    return None, False
