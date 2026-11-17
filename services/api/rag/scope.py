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

#: A range like "2017-2019", "2017–2019", or "2017 to 2019". Deliberately NOT
#: restricted to valid NHIS years (unlike _YEAR): a researcher comparing
#: "2023-2025" needs 2024 and 2025 recognised as requested, so the answer can
#: say plainly that NHIS has not released those years, rather than silently
#: dropping them and finding nothing to explain.
_YEAR_RANGE = re.compile(r"\b(\d{4})\s*(?:-|\u2013|to)\s*(\d{4})\b")
_ANY_FOUR_DIGIT_YEAR = re.compile(r"\b(\d{4})\b")

#: A range spanning more than this many years is almost certainly not a
#: survey-year range (e.g. "1000-2000 participants"), so it is ignored.
_MAX_RANGE_SPAN = 15


def infer_year(text: str) -> int | None:
    """The single survey year named in `text`, or None if zero or several."""
    years = {int(match) for match in _YEAR.findall(text or "")}
    return next(iter(years)) if len(years) == 1 else None


def infer_years(text: str) -> list[int]:
    """Every distinct year `text` mentions, expanding ranges, for detecting a
    cross-year comparison question.

    Unlike `infer_year`, this is not restricted to years NHIS actually
    covers — "2023-2025" must surface 2024 and 2025 even though neither is a
    real survey year, so the caller can explain their absence rather than
    silently ignoring them. A plain year appearing inside an already-matched
    range is not counted twice.
    """
    text = text or ""
    years: set[int] = set()
    consumed: list[tuple[int, int]] = []

    for match in _YEAR_RANGE.finditer(text):
        # Consumed regardless of whether it expands to years: "X-Y" was
        # written as one compound expression, so a rejected range (endpoints
        # outside a plausible year, or too wide, e.g. "1000-2000
        # participants") must not leak either endpoint through as a
        # standalone year.
        consumed.append(match.span())
        start, end = int(match.group(1)), int(match.group(2))
        if (
            1900 <= start <= 2099
            and 1900 <= end <= 2099
            and start <= end
            and end - start <= _MAX_RANGE_SPAN
        ):
            years.update(range(start, end + 1))

    for match in _ANY_FOUR_DIGIT_YEAR.finditer(text):
        if any(span_start <= match.start() < span_end for span_start, span_end in consumed):
            continue
        year = int(match.group(1))
        if 1900 <= year <= 2099:
            years.add(year)

    return sorted(years)


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
