"""Ingestion abort guard (Requirement 9.1, 9.2, 9.3).

SAFETY-CRITICAL.

Refuses to load a survey year whose codebook parsed to implausibly few
variables, rather than indexing partial data and serving confident wrong
answers from it. In earlier prototyping three of five years silently parsed to
zero variables and the pipeline carried on regardless — this guard is the
reason that cannot recur.

Format-agnostic by design: it accepts only a year and a count, so the same
guard serves both the 2019+ parser and the 2017-2018 legacy parser without
either importing the other (Requirement 12.2).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AbortDecision:
    """Outcome of the guard. `report` always names the year and the count."""

    survey_year: int
    parsed_count: int
    threshold: int
    should_abort: bool

    @property
    def report(self) -> str:
        if self.should_abort:
            return (
                f"ABORT {self.survey_year}: parsed only {self.parsed_count} "
                f"variables, below the minimum of {self.threshold}. "
                f"No rows written for {self.survey_year}."
            )
        return (
            f"OK {self.survey_year}: parsed {self.parsed_count} variables "
            f"(minimum {self.threshold})."
        )


def check_abort(survey_year: int, parsed_count: int, threshold: int) -> AbortDecision:
    """Decide whether to load `survey_year` given how many variables parsed.

    Aborts when `parsed_count < threshold`. A real NHIS adult codebook yields
    roughly 600-650 variables, so the default threshold of 100 catches a
    wholesale parser failure (a format change, a corrupt download, the wrong
    file) while leaving room for a genuinely small year.

    Raises ValueError on a negative count, which indicates a caller bug rather
    than a parsing failure and should not be silently treated as an abort.
    """
    if parsed_count < 0:
        raise ValueError(f"parsed_count cannot be negative, got {parsed_count}")
    if threshold < 0:
        raise ValueError(f"threshold cannot be negative, got {threshold}")

    return AbortDecision(
        survey_year=survey_year,
        parsed_count=parsed_count,
        threshold=threshold,
        should_abort=parsed_count < threshold,
    )
