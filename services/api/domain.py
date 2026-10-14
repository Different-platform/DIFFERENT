"""Core domain types shared by the pipeline and the API surface.

Field names here deliberately match the frontend contract in
`ui-template/src/lib/differ/types.ts` (snake_case, `section` rather than
`section_name` on a citation). Keeping them identical is what lets the real
SSE reader drop into the UI without touching a component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

Verdict = Literal["MEASURED", "RELATED", "ABSENT"]
RouteIntent = Literal["codebook", "data", "both"]


@dataclass(frozen=True)
class ValueLabel:
    code: Decimal
    label: str
    is_sentinel: bool
    frequency: int | None = None
    percent: Decimal | None = None


@dataclass(frozen=True)
class Citation:
    """What the UI renders on a citation card (Requirement 2.1).

    All four of variable_name, section, page, and survey_year are required —
    a citation missing any of them cannot be checked against the codebook,
    which defeats the point.
    """

    variable_name: str
    section: str
    page: int
    survey_year: int
    description: str | None = None
    question_text: str | None = None

    def to_dict(self) -> dict:
        return {
            "variable_name": self.variable_name,
            "section": self.section,
            "page": self.page,
            "survey_year": self.survey_year,
            "description": self.description,
            "question_text": self.question_text,
        }


@dataclass
class Hit:
    """One retrieved codebook variable with its similarity score."""

    variable_name: str
    survey_year: int
    page: int
    similarity: float

    section_code: str | None = None
    section_name: str | None = None
    module: str | None = None
    description: str | None = None
    question_text: str | None = None
    keywords: str | None = None
    universe: str | None = None
    universe_description: str | None = None
    data_type: str | None = None
    is_recode: bool = False
    value_labels: list[ValueLabel] = field(default_factory=list)

    @property
    def substantive_labels(self) -> list[ValueLabel]:
        return [vl for vl in self.value_labels if not vl.is_sentinel]

    @property
    def sentinel_labels(self) -> list[ValueLabel]:
        return [vl for vl in self.value_labels if vl.is_sentinel]

    def to_citation(self) -> Citation:
        return Citation(
            variable_name=self.variable_name,
            section=self.section_name or self.section_code or "Uncategorized",
            page=self.page,
            survey_year=self.survey_year,
            description=self.description,
            question_text=self.question_text or None,
        )


@dataclass(frozen=True)
class SectionHit:
    code: str
    name: str
    survey_year: int
    similarity: float
    n_variables: int | None = None
