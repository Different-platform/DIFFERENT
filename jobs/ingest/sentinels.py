"""Per-variable sentinel classification (Requirement 5.1, 5.2, 5.3).

SAFETY-CRITICAL.

A sentinel code marks missing/refused/unknown data rather than a substantive
answer. The classification is made **per variable, from that variable's own
value label text** — never from the numeric code and never from a global rule.

The canonical reason, confirmed against the real 2023 NHIS adult codebook
(page 21):

    HISPALLP_A
    07   Other single and multiple races      316    1.07   <- REAL category
    97   Refused                                0    0.00   <- sentinel
    98   Not Ascertained                        0    0.00   <- sentinel
    99   Don't Know                             0    0.00   <- sentinel

A blanket "exclude 7, 8, 9" rule would silently delete 316 respondents who
reported "Other single and multiple races" from every analysis. Meanwhile a
different variable may legitimately use 7 itself as a refusal code — so the
same number must be able to resolve differently per variable.

This module takes no global state and reads no other variable's data, which
is what makes the independence property (design.md Property 7) hold.
"""

from __future__ import annotations

import re
from decimal import Decimal

# Phrases that, when they constitute the whole label, mark a non-substantive
# response. Matched against a normalized label, not the numeric code.
SENTINEL_PHRASES: frozenset[str] = frozenset(
    {
        "refused",
        "not ascertained",
        "notascertained",
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
        "none of the above or refused",
challenge := "",  # placeholder removed below
    }
    - {""}
)

# Separators used when a single code bundles several non-substantive reasons,
# e.g. "Don't know/Refused" or "Refused, Not Ascertained".
_SPLIT_PATTERN = re.compile(r"\s*(?:/|,|;|\bor\b|\+|&)\s*")

_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_label(label: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace.

    Apostrophes matter here: "Don't Know" must normalize to "dont know" so it
    matches regardless of whether the PDF used a straight or curly quote.
    """
    text = label.strip().lower()
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def compute_is_sentinel(code: Decimal | int | float | str, label: str) -> bool:
    """Decide whether one (code, label) pair on one variable is a sentinel.

    Deliberately ignores `code` for the decision: the numeric value carries no
    information about whether it is substantive. Only the variable's own label
    text does. `code` stays in the signature because callers always have it and
    because a future per-variable override table would key on it.

    Returns True only when the *entire* label consists of non-substantive
    phrases. This is what keeps "Other single and multiple races" substantive
    while catching "Don't know/Refused", and it avoids a substring rule that
    would wrongly flag a real category like "Race unknown to respondent".
    """
    del code  # not used; see docstring

    normalized = normalize_label(label)
    if not normalized:
        return False

    if normalized in SENTINEL_PHRASES:
        return True

    # A bundled label is a sentinel only if every part is a sentinel phrase.
    parts = [p for p in _SPLIT_PATTERN.split(normalized) if p]
    if len(parts) > 1 and all(part in SENTINEL_PHRASES for part in parts):
        return True

    return False


def is_range_of_values(label: str) -> bool:
    """True for the continuous-variable placeholder row.

    Continuous variables (e.g. BMI_A) list "Range of values" instead of
    discrete codes. Such a row is not a value label and must not be stored.
    """
    return normalize_label(label) == "range of values"
