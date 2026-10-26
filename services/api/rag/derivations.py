"""Known derived measures and the variables they are computed from.

Some quantities researchers ask for exist in NHIS only as their ingredients.
Body mass index in the 2023 file is published as a 4-category recode; the
continuous value is height and weight combined. Vector search is a poor way to
find those ingredients, because they are not about "BMI" in their own text:
the height variable ranked 3rd for "mean BMI" in four years and below 25th in
the fifth, and dropped out entirely for "mean body mass index". When an input
is missing from the candidates the question reads as "not measured", which is
wrong, and it fails differently by year and by phrasing.

So the inputs of a derivation are added explicitly. This is a short table on
purpose: every entry is domain knowledge that can be read and checked, which an
embedding or a model call cannot offer. Add an entry only for a formula that
is standard and unambiguous. It only adds *candidates*. The judge still has to
decide the concept is measured, the SQL generator is still given the real
codes, and the verifier still gates the query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Derivation:
    name: str
    pattern: re.Pattern[str]
    inputs: tuple[str, ...]


DERIVATIONS: tuple[Derivation, ...] = (
    Derivation(
        name="body mass index",
        # "BMI" and the spelled-out name, but not BMICAT_A and friends: the
        # word boundary stops "bmi" matching inside a longer identifier.
        pattern=re.compile(r"\b(bmi|body[\s-]+mass[\s-]+index)\b", re.IGNORECASE),
        # Height in inches and weight in pounds: BMI = 703 * lb / in^2.
        inputs=("HEIGHTTC_A", "WEIGHTLBTC_A"),
    ),
)


def required_inputs(question: str) -> list[str]:
    """Variable names to add as candidates for the derived measures in `question`."""
    names: list[str] = []
    for derivation in DERIVATIONS:
        if derivation.pattern.search(question or ""):
            for variable in derivation.inputs:
                if variable not in names:
                    names.append(variable)
    return names
