"""Shared record types produced by every parser track.

Both the 2019+ parser and the 2017-2018 legacy parser emit these same types,
so downstream embedding and loading code is written once. The two tracks
depend on this module rather than on each other, which is what keeps them
isolated (Requirement 12.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

SourceEra = str  # '2019plus' | 'legacy_2017_2018'


@dataclass(frozen=True)
class ParsedValueLabel:
    """One row of a variable's value-label table."""

    code: Decimal
    label: str
    is_sentinel: bool
    frequency: int | None = None
    percent: Decimal | None = None


@dataclass
class ParsedVariable:
    """One codebook variable for one survey year."""

    survey_year: int
    variable_name: str
    page: int
    source_era: SourceEra

    module: str | None = None
    section_code: str | None = None
    section_name: str | None = None
    data_type: str | None = None
    question_text: str | None = None
    description: str | None = None
    universe: str | None = None
    universe_description: str | None = None
    sources: str | None = None
    keywords: str | None = None
    notes: str | None = None
    is_recode: bool = False

    # True when the codebook printed "Range of values" instead of discrete
    # codes, i.e. a continuous measure such as a weight or an age.
    is_continuous: bool = False

    value_labels: list[ParsedValueLabel] = field(default_factory=list)

    @property
    def substantive_labels(self) -> list[ParsedValueLabel]:
        return [vl for vl in self.value_labels if not vl.is_sentinel]

    @property
    def sentinel_labels(self) -> list[ParsedValueLabel]:
        return [vl for vl in self.value_labels if vl.is_sentinel]


def build_embed_text(variable: ParsedVariable) -> str:
    """Compose the exact text that gets embedded for retrieval.

    Stored alongside the vector as `embed_text` so a retrieval result can
    always be traced back to what was actually indexed.

    Sentinel labels are deliberately excluded. Nearly every variable carries
    "Refused" / "Not Ascertained" / "Don't Know", so including them pushes all
    variables toward each other in vector space and dilutes the signal that
    distinguishes them. Only substantive response options describe what a
    variable actually measures.
    """
    lines: list[str] = []

    headline = variable.variable_name
    if variable.description:
        headline = f"{variable.variable_name}: {variable.description}"
    lines.append(headline)

    if variable.section_name:
        lines.append(f"Section: {variable.section_name}")
    if variable.module:
        lines.append(f"Module: {variable.module}")
    if variable.question_text:
        lines.append(f"Question: {variable.question_text}")
    if variable.universe_description:
        lines.append(f"Asked of: {variable.universe_description}")
    if variable.keywords:
        lines.append(f"Keywords: {variable.keywords}")

    if variable.is_continuous:
        lines.append("Responses: continuous range of values")
    else:
        labels = [vl.label for vl in variable.substantive_labels]
        if labels:
            lines.append("Responses: " + "; ".join(labels))

    return "\n".join(lines)
