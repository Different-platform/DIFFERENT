"""Parser for the redesigned (2019+) NHIS adult codebook PDFs.

SAFETY-CRITICAL — this is where sentinel codes are determined.

Layout, as produced by PyMuPDF with sort=True (verified against the real
2023 adult codebook, printed page 21):

    Variable Information:
    Variable:                 HISPALLP_A
    Module:                   Roster
    Section:                  HHC : Household Composition
    File(s):                  Adult
    Data Type:                Numeric
    Length:                   2
    Question Text:
    Description:              Single and multiple race groups with Hispanic origin
    Recode:                   Yes
    Universe:                 HHSTAT_A=1
    Universe Description:     Sample adults 18+
    Sources:                  NATORG; RACE; ...
    Keywords:                 race; Hispanic origin
    Notes:                    This variable is a public use recode of ...
                              continuation lines are indented with no label
    Unweighted frequencies:
    HISPALLP_A    Single and multiple race groups with Hispanic origin
    Code          Description                      Frequency    Percent
    01            Hispanic                              4418      14.97
    ...
    Frequency Missing:
                              Page 21

Field values sit on the same line as their label; wrapped values continue on
following unlabelled lines. Value-label rows are only read *after* the
"Code / Description / Frequency / Percent" header, which is what prevents the
repeated "VARNAME  description" line above it from being mistaken for a row.

This module must never import from parser_legacy_2017_2018 (Requirement 12.2).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf

from jobs.ingest.records import ParsedValueLabel, ParsedVariable
from jobs.ingest.sentinels import compute_is_sentinel, is_range_of_values

SOURCE_ERA = "2019plus"

#: Labels the codebook uses. Anything else on a label-looking line is treated
#: as a wrapped continuation of the previous field.
KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "Variable",
        "Module",
        "Section",
        "File(s)",
        "Data Type",
        "Length",
        "Question Text",
        "Description",
        "Recode",
        "Universe",
        "Universe Description",
        "Sources",
        "Question ID",
        "Keywords",
        "Notes",
        "Evaluation Report",
        "Frequency Missing",
    }
)

_FIELD_START = re.compile(r"^(?P<label>[A-Z][A-Za-z()' ]{0,30}):(?P<value>.*)$")
_TABLE_HEADER = re.compile(r"^\s*Code\s+Description\s+Frequency\s+Percent\s*$")
_PAGE_FOOTER = re.compile(r"^\s*Page\s+(?P<number>\d+)\s*$")

# Full row: code, description, frequency, percent — separated by column gaps.
_ROW_WITH_STATS = re.compile(
    r"^(?P<code>\S+)\s{2,}(?P<label>.+?)\s{2,}(?P<freq>\d[\d,]*)\s+(?P<pct>\d+\.\d+)\s*$"
)
# Row lacking frequency/percent (some recodes print codes only).
_ROW_CODE_ONLY = re.compile(r"^(?P<code>\S+)\s{2,}(?P<label>.+?)\s*$")

_SECTION = re.compile(r"^(?P<code>\S+)\s*:\s*(?P<name>.+)$")

_VARIABLE_INFO_MARKER = "Variable Information:"
_FREQUENCIES_MARKER = "Unweighted frequencies:"


def _printed_page_number(text: str) -> int | None:
    """The page number printed in the footer, which is what a citation cites."""
    for line in reversed(text.splitlines()):
        match = _PAGE_FOOTER.match(line.rstrip())
        if match:
            return int(match.group("number"))
    return None


def parse_fields(text: str) -> dict[str, str]:
    """Extract the labelled field block, joining wrapped continuation lines."""
    fields: dict[str, str] = {}
    current: str | None = None
    started = False

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not started:
            if stripped == _VARIABLE_INFO_MARKER:
                started = True
            continue

        if stripped.startswith(_FREQUENCIES_MARKER):
            break
        if not stripped:
            continue

        match = _FIELD_START.match(stripped)
        if match and match.group("label") in KNOWN_FIELDS:
            current = match.group("label")
            fields[current] = match.group("value").strip()
        elif current is not None:
            fields[current] = f"{fields[current]} {stripped}".strip()

    return fields


def parse_value_label_rows(text: str) -> tuple[list[tuple[Decimal, str, int | None, Decimal | None]], bool]:
    """Parse the value-label table on one page.

    Returns the rows plus a flag indicating the variable is continuous (the
    codebook printed "Range of values" rather than discrete codes).

    Only lines after the column header are considered, so the repeated
    "VARNAME   description" line that precedes it is never read as a row.
    """
    rows: list[tuple[Decimal, str, int | None, Decimal | None]] = []
    in_table = False
    continuous = False

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if _TABLE_HEADER.match(line):
            in_table = True
            continue
        if not in_table or not stripped:
            continue
        if stripped.startswith("Frequency Missing"):
            break
        if _PAGE_FOOTER.match(line):
            break
        if is_range_of_values(stripped):
            continuous = True
            continue

        parsed = _parse_row(line)
        if parsed is not None:
            rows.append(parsed)

    return rows, continuous


def _parse_row(line: str) -> tuple[Decimal, str, int | None, Decimal | None] | None:
    """Parse one table row, or return None if the line is not a row.

    A non-numeric leading token means the line is a wrapped continuation of
    the previous description, not a new code.
    """
    match = _ROW_WITH_STATS.match(line)
    if match:
        code = _to_decimal(match.group("code"))
        if code is None:
            return None
        frequency = int(match.group("freq").replace(",", ""))
        percent = _to_decimal(match.group("pct"))
        return code, match.group("label").strip(), frequency, percent

    match = _ROW_CODE_ONLY.match(line)
    if match:
        code = _to_decimal(match.group("code"))
        if code is None:
            return None
        return code, match.group("label").strip(), None, None

    return None


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _build_variable(
    fields: dict[str, str], page: int, survey_year: int
) -> ParsedVariable:
    section_code: str | None = None
    section_name: str | None = None
    if raw_section := fields.get("Section"):
        match = _SECTION.match(raw_section)
        if match:
            section_code = match.group("code").strip()
            section_name = match.group("name").strip()
        else:
            section_name = raw_section.strip()

    return ParsedVariable(
        survey_year=survey_year,
        variable_name=fields["Variable"].strip(),
        page=page,
        source_era=SOURCE_ERA,
        module=fields.get("Module") or None,
        section_code=section_code,
        section_name=section_name,
        data_type=fields.get("Data Type") or None,
        question_text=fields.get("Question Text") or None,
        description=fields.get("Description") or None,
        universe=fields.get("Universe") or None,
        universe_description=fields.get("Universe Description") or None,
        sources=fields.get("Sources") or None,
        keywords=fields.get("Keywords") or None,
        notes=fields.get("Notes") or None,
        is_recode=(fields.get("Recode", "").strip().lower() == "yes"),
    )


def _attach_rows(
    variable: ParsedVariable,
    rows: list[tuple[Decimal, str, int | None, Decimal | None]],
) -> None:
    """Add value labels, classifying each as sentinel from its own label text."""
    seen = {vl.code for vl in variable.value_labels}
    for code, label, frequency, percent in rows:
        if code in seen:
            continue
        seen.add(code)
        variable.value_labels.append(
            ParsedValueLabel(
                code=code,
                label=label,
                is_sentinel=compute_is_sentinel(code, label),
                frequency=frequency,
                percent=percent,
            )
        )


def parse_codebook(pdf_path: Path, survey_year: int) -> list[ParsedVariable]:
    """Parse every variable out of a 2019+ adult codebook PDF.

    Pages without a `Variable:` label but carrying table rows are treated as
    continuations of the preceding variable, which is how long value-label
    tables that spill across a page boundary stay attached to their variable.
    """
    document = pymupdf.open(pdf_path)
    variables: list[ParsedVariable] = []
    current: ParsedVariable | None = None

    try:
        for index in range(document.page_count):
            text = document[index].get_text(sort=True)
            page = _printed_page_number(text) or (index + 1)

            fields = parse_fields(text)
            rows, continuous = parse_value_label_rows(text)

            if fields.get("Variable"):
                current = _build_variable(fields, page, survey_year)
                if continuous:
                    current.is_continuous = True
                variables.append(current)
                _attach_rows(current, rows)
            elif current is not None and rows:
                _attach_rows(current, rows)
                if continuous:
                    current.is_continuous = True
    finally:
        document.close()

    return variables
