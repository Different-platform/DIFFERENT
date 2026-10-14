"""Per-variable sentinel classification (Requirement 5.1, 5.2, 5.3).

SAFETY-CRITICAL.

A sentinel code marks missing/refused/unknown data rather than a substantive
answer. The classification is made **per variable, from that variable's own
value label text** — never from the numeric code and never from a global rule.

The canonical reason, confirmed against the real 2023 NHIS adult codebook
(printed page 21):

    HISPALLP_A
    07   Other single and multiple races      316    1.07   <- REAL category
    97   Refused                                0    0.00   <- sentinel
    98   Not Ascertained                        0    0.00   <- sentinel
    99   Don't Know                             0    0.00   <- sentinel

A blanket "exclude 7, 8, 9" rule would silently delete the 316 respondents who
reported "Other single and multiple races" from every analysis. Meanwhile a
different variable may legitimately use 7 itself as a refusal code — so the
same number must be able to resolve differently per variable.

This module holds no global state and reads no other variable's data, which is
what makes the independence property (design.md Property 7) hold.

Bias note: when a label is ambiguous this errs toward *substantive*. Wrongly
marking a real category as a sentinel deletes respondents silently; wrongly
keeping a sentinel is visible in the output distribution.
"""

from __future__ import annotations

import re
from decimal import Decimal

# Phrases that, when they account for the whole label, mark a non-substantive
# response. Compared against a normalized label, never against the code.
SENTINEL_PHRASES: frozenset[str] = frozenset(
    {
        "refused",
        "not ascertained",
        "dont know",
        "do not know",
        "unknown",
        "not available",
        "not applicable",
        "no response",
        "missing",
        "blank",
        "undefined",
        "not reported",
        "not stated",
    }
)

# Separators used when one code bundles several non-substantive reasons,
# e.g. "Don't know/Refused" or "Refused, Not Ascertained". Note "and" is
# deliberately absent: "AIAN and any other group" is a real category.
_SEPARATORS = re.compile(r"\s*(?:/|,|;|\bor\b|\+|&)\s*")

_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


def _fold(text: str) -> str:
    """Lowercase and drop apostrophes, preserving separator characters.

    Apostrophes are *deleted* rather than replaced with a space, so that
    "Don't Know" folds to "dont know" and not "don t know". Getting this
    wrong silently reclassifies every "Don't Know" code in the codebook as a
    substantive answer — roughly 590 labels in the 2023 adult file alone, and
    the specific defect this module regressed to twice already.
    """
    folded = text.strip().lower()
    folded = folded.replace("\u2019", "'").replace("\u2018", "'")
    return folded.replace("'", "")


def _scrub(text: str) -> str:
    """Strip remaining punctuation and collapse whitespace."""
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", text)).strip()


def normalize_label(label: str) -> str:
    """Fully normalized form of a value label, for comparison and lookup."""
    return _scrub(_fold(label))


def compute_is_sentinel(code: Decimal | int | float | str, label: str) -> bool:
    """Decide whether one (code, label) pair on one variable is a sentinel.

    Deliberately ignores `code` for the decision: the numeric value carries no
    information about whether the response is substantive — only the
    variable's own label text does. `code` remains in the signature because
    every caller has it and a future per-variable override table would key
    on it.

    Returns True only when *every* component of the label is a sentinel
    phrase. That keeps "Other single and multiple races" substantive while
    still catching "Don't know/Refused", and it avoids a substring rule that
    would wrongly flag a real category such as "Race unknown to respondent".
    """
    del code  # not used for the decision; see docstring

    folded = _fold(label)
    if not folded.strip():
        return False

    # Split while separators still exist, then scrub each component. Scrubbing
    # first would destroy the separators this relies on.
    parts = [_scrub(part) for part in _SEPARATORS.split(folded)]
    parts = [part for part in parts if part]
    if not parts:
        return False

    return all(part in SENTINEL_PHRASES for part in parts)


def is_range_of_values(label: str) -> bool:
    """True for the continuous-variable placeholder row.

    Continuous variables print "Range of values" in place of discrete codes.
    Such a row is not a value label and must not be stored as one.

    Matched as a prefix, not an exact string: the codebook prints the phrase
    in the description column with the frequency and percent columns trailing
    it, e.g.

        "Range of values                       29522      100.00"
    """
    return normalize_label(label).startswith("range of values")
