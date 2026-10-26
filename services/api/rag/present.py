"""Turn a verified SQL result into something a researcher can use.

The SQL returns what SQL returns: category *codes* (1, 2, 3, 4) and raw
fractions (0.0161). A researcher needs "Underweight 1.6%", which population
that covers, and what was left out. This module does that translation.

It is deliberately deterministic, with no model call. Every number in the
summary is copied from the verified result, so the prose cannot contain a
figure the database did not produce, and it costs no tokens. Rounding is the
only transformation applied to a value, and the exact query is shown beside
the result so the unrounded computation stays inspectable.

Everything here runs *after* verification and execution succeed. Nothing in
this module can create a number; it can only describe one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from services.api.domain import Hit

#: Weight and design columns: plumbing, never the thing being measured.
_PLUMBING = frozenset({"wtfa_a", "wtia_a", "wtfa_sa", "wtia_sa", "pstrat", "ppsu"})

#: The universe text that means "everyone in the Sample Adult file". Anything
#: narrower is worth telling the reader, because a variable asked of a
#: conditional subsample is easy to misread as covering all adults.
_GENERIC_UNIVERSE = "sample adults 18+"

_MAX_EXCLUSION_LABELS = 6


@dataclass(frozen=True)
class Presented:
    columns: list[str]
    rows: list[list[Any]]
    summary: str


def is_empty(rows: list[list[Any]]) -> bool:
    """True when the query matched nobody (no rows, or only NULLs)."""
    return not rows or all(value is None for row in rows for value in row)


# ---------------------------------------------------------------------------
# Reading the SQL
# ---------------------------------------------------------------------------
def variables_in(text: str | None, hits: list[Hit]) -> list[Hit]:
    """Retrieved variables whose names appear in `text`, excluding plumbing."""
    found: list[Hit] = []
    seen: set[str] = set()
    for hit in hits:
        key = hit.variable_name.lower()
        if key in _PLUMBING or key in seen:
            continue
        if re.search(rf"\b{re.escape(hit.variable_name)}\b", text or "", re.IGNORECASE):
            seen.add(key)
            found.append(hit)
    return found


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _select_list(sql: str) -> str:
    """Text between the first SELECT and its FROM, ignoring nested subqueries."""
    start = re.search(r"\bselect\b", sql, re.IGNORECASE)
    if start is None:
        return ""
    lowered = sql.lower()
    depth = 0
    for i in range(start.end(), len(sql)):
        char = sql[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif (
            depth == 0
            and lowered.startswith("from", i)
            and not _is_word_char(lowered[i - 1])
            and (i + 4 >= len(lowered) or not _is_word_char(lowered[i + 4]))
        ):
            return sql[start.end() : i]
    return sql[start.end() :]


def _group_by_text(sql: str) -> str | None:
    match = re.search(
        r"\bgroup\s+by\b(.*?)(?:\border\s+by\b|\bhaving\b|\blimit\b|$)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Reading the result columns
# ---------------------------------------------------------------------------
def _metric_kind(name: str) -> str | None:
    lowered = name.lower()
    if any(token in lowered for token in ("proportion", "share", "percent", "pct")):
        return "percent"
    if any(token in lowered for token in ("mean", "average", "avg")):
        return "mean"
    if "count" in lowered or "total" in lowered or lowered in ("weighted_n", "n"):
        return "count"
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _code_text(value: Any) -> str:
    number = _to_decimal(value)
    if number is None:
        return str(value)
    return str(int(number)) if number == number.to_integral_value() else str(number)


def _label_for(value: Any, hit: Hit) -> str | None:
    number = _to_decimal(value)
    if number is None:
        return None
    for vl in hit.value_labels:
        if vl.code == number:
            return vl.label.strip()
    return None


def _coded_cell(value: Any, hit: Hit) -> str:
    label = _label_for(value, hit)
    return f"{label} ({_code_text(value)})" if label else _code_text(value)


def _plain_label(value: Any, hit: Hit) -> str:
    return _label_for(value, hit) or f"code {_code_text(value)}"


def _format_value(value: Any, kind: str | None, *, as_fraction: bool) -> Any:
    number = _to_decimal(value)
    if number is None:
        return value
    if kind == "percent":
        return f"{float(number) * (100 if as_fraction else 1):.1f}%"
    if kind == "mean":
        return round(float(number), 2)
    if kind == "count":
        return f"{round(float(number)):,}"
    return round(float(number), 4) if number != number.to_integral_value() else int(number)


def _case_target(sql: str, hits: list[Hit]) -> tuple[Hit, str] | None:
    """The category a "share who are X" query counts, e.g. (BMICAT_A, "Obese").

    Reads `CASE WHEN col = code`. Without this the summary can only say "the
    share is 33.8%" and leaves the reader to guess which category that was.
    """
    match = re.search(
        r"\bcase\s+when\s+(\w+)\s*=\s*(-?\d+(?:\.\d+)?)", sql, re.IGNORECASE
    )
    if match is None:
        return None
    hit = next(
        (h for h in hits if h.variable_name.lower() == match.group(1).lower()), None
    )
    label = _label_for(match.group(2), hit) if hit is not None else None
    return (hit, label) if hit is not None and label else None


def _describe(hit: Hit) -> str:
    return f"{hit.variable_name} ({hit.description})" if hit.description else hit.variable_name


def _join(hits: list[Hit]) -> str:
    return " and ".join(_describe(hit) for hit in hits)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def present_result(
    columns: list[str],
    rows: list[list[Any]],
    *,
    sql: str,
    hits: list[Hit],
    year: int,
    year_assumed: bool = False,
) -> Presented:
    """Label the codes, format the numbers, and describe what was computed."""
    by_name = {hit.variable_name.lower(): hit for hit in hits}
    group_text = _group_by_text(sql)
    group_vars = variables_in(group_text, hits) if group_text else []
    group_names = {hit.variable_name.lower() for hit in group_vars}
    select_vars = variables_in(_select_list(sql), hits)
    measure_vars = [h for h in select_vars if h.variable_name.lower() not in group_names]
    used = variables_in(sql, hits)

    # A scalar built from two or more ungrouped columns is a derived measure
    # (BMI from height and weight): no single NHIS variable holds it.
    derived = group_text is None and len(measure_vars) >= 2

    # Which result columns hold category codes? Match by name first; if the
    # model aliased the category column, fall back to the single GROUP BY
    # variable.
    coded: dict[int, Hit] = {}
    for index, name in enumerate(columns):
        hit = by_name.get(name.lower())
        if hit is not None and hit.value_labels:
            coded[index] = hit
    if (
        not coded
        and len(group_vars) == 1
        and group_vars[0].value_labels
        and columns
        and _metric_kind(columns[0]) is None
    ):
        coded[0] = group_vars[0]

    kinds = [None if i in coded else _metric_kind(name) for i, name in enumerate(columns)]

    fraction_cols: set[int] = set()
    for index, kind in enumerate(kinds):
        if kind != "percent":
            continue
        values = [_to_decimal(row[index]) for row in rows]
        numbers = [float(v) for v in values if v is not None]
        if numbers and all(0 <= n <= 1.0000001 for n in numbers):
            fraction_cols.add(index)

    out_rows: list[list[Any]] = []
    for row in rows:
        cells: list[Any] = []
        for index, value in enumerate(row):
            if index in coded:
                cells.append(_coded_cell(value, coded[index]))
            else:
                cells.append(
                    _format_value(value, kinds[index], as_fraction=index in fraction_cols)
                )
        out_rows.append(cells)

    headers: list[str] = []
    for index, name in enumerate(columns):
        if index in coded:
            headers.append(coded[index].variable_name.upper())
        elif kinds[index] == "percent":
            headers.append("Percent of adults (weighted)")
        elif kinds[index] == "mean":
            headers.append("Weighted mean")
        elif kinds[index] == "count":
            headers.append("Estimated adults (weighted count)")
        else:
            headers.append(name.replace("_", " ").capitalize())

    # "SUM(...) OVER (PARTITION BY sex)" means the percentages are shares
    # *within* each sex rather than shares of everyone; the wording differs.
    partition = re.search(r"\bpartition\s+by\b(.*?)\)", sql, re.IGNORECASE | re.DOTALL)
    partition_vars = variables_in(partition.group(1), hits) if partition else []

    summary = _summarize(
        target=_case_target(sql, hits),
        partition_vars=partition_vars,
        columns=columns,
        raw_rows=rows,
        shown_rows=out_rows,
        coded=coded,
        kinds=kinds,
        group_vars=group_vars,
        measure_vars=measure_vars,
        used=used,
        derived=derived,
        year=year,
        year_assumed=year_assumed,
    )
    return Presented(columns=headers, rows=out_rows, summary=summary)


def _summarize(
    *,
    columns: list[str],
    raw_rows: list[list[Any]],
    shown_rows: list[list[Any]],
    coded: dict[int, Hit],
    kinds: list[str | None],
    group_vars: list[Hit],
    measure_vars: list[Hit],
    used: list[Hit],
    partition_vars: list[Hit],
    target: tuple[Hit, str] | None,
    derived: bool,
    year: int,
    year_assumed: bool,
) -> str:
    scope = f"U.S. adults in {year}"
    metric = next((i for i, kind in enumerate(kinds) if kind is not None), None)
    metric_kind = kinds[metric] if metric is not None else None
    sentences: list[str] = []

    if year_assumed:
        sentences.append(
            f"No survey year was selected, so I used {year}, the most recent."
        )

    if len(coded) >= 2 and metric is not None:
        # A two-way table. Every coded column has to appear in each label, or
        # "Male 0.5%, Male 13.0%" reads as nonsense.
        order = sorted(coded)
        labelled = [
            [_plain_label(raw[i], coded[i]) for i in order] for raw in raw_rows
        ]
        values = [shown[metric] for shown in shown_rows]
        names = [coded[i].variable_name for i in order]
        pivot = next(
            (
                position
                for position, i in enumerate(order)
                if any(p.variable_name == coded[i].variable_name for p in partition_vars)
            ),
            None,
        )
        if pivot is not None and metric_kind == "percent":
            groups: dict[str, list[str]] = {}
            for labels, value in zip(labelled, values, strict=True):
                rest = ", ".join(l for j, l in enumerate(labels) if j != pivot)
                groups.setdefault(labels[pivot], []).append(f"{rest} {value}")
            listing = "; ".join(f"{key}: {', '.join(parts)}" for key, parts in groups.items())
            other = [coded[i] for j, i in enumerate(order) if j != pivot]
            sentences.append(
                f"Among {scope} (population-weighted), the distribution of "
                f"{_join(other)} within each {_describe(coded[order[pivot]])} "
                f"group is: {listing}. Percentages add up to 100% within each group."
            )
        elif metric_kind == "percent":
            listing = "; ".join(
                f"{', '.join(labels)} {value}"
                for labels, value in zip(labelled, values, strict=True)
            )
            sentences.append(
                f"Share of {scope} in each combination of {' and '.join(names)} "
                f"(shares add up to 100% across all combinations): {listing}."
            )
        else:
            listing = "; ".join(
                f"{', '.join(labels)}: {value}"
                for labels, value in zip(labelled, values, strict=True)
            )
            sentences.append(
                f"Results for {scope} by {' and '.join(names)}: {listing}."
            )
    elif coded and metric is not None:
        subject = coded[min(coded)]
        first = min(coded)
        pairs = [
            (_plain_label(raw[first], subject), shown[metric])
            for raw, shown in zip(raw_rows, shown_rows, strict=True)
        ]
        listing = (
            "; ".join(f"{label} {value}" for label, value in pairs)
            if metric_kind != "percent"
            else ", ".join(f"{label} {value}" for label, value in pairs)
        )
        if metric_kind == "percent" and target is not None:
            # "Share who are Obese, by sex": each figure is a share *within*
            # its group, so they do not add up to 100% and there is no
            # "largest group" to announce.
            sentences.append(
                f"Among {scope} (population-weighted), the share classified as "
                f"\"{target[1]}\" on {_describe(target[0])}, within each "
                f"{_describe(subject)} group: {listing}."
            )
        elif metric_kind == "percent":
            sentences.append(
                f"Among {scope} (population-weighted), the distribution of "
                f"{_describe(subject)} is: {listing}."
            )
            raw_values = [_to_decimal(raw[metric]) for raw in raw_rows]
            known = [v for v in raw_values if v is not None]
            # Only a true distribution (shares summing to ~100%) has a
            # meaningful "largest group".
            adds_to_whole = bool(known) and (
                abs(sum(known) - 1) < 0.01 or abs(sum(known) - 100) < 1
            )
            if len(pairs) > 1 and adds_to_whole:
                best = max(
                    range(len(pairs)),
                    key=lambda i: raw_values[i] if raw_values[i] is not None else -1,
                )
                sentences.append(
                    f"The largest group is {pairs[best][0]} ({pairs[best][1]})."
                )
        elif metric_kind == "mean":
            what = _join(measure_vars) if measure_vars else "the measure"
            sentences.append(
                f"The population-weighted mean of {what} for {scope}, by "
                f"{_describe(subject)}: {listing}."
            )
        else:
            sentences.append(
                f"Estimated results for {scope} by {_describe(subject)}: {listing}."
            )
    elif len(shown_rows) == 1 and metric is not None and len(columns) == 1:
        value = shown_rows[0][metric]
        if metric_kind == "mean" and derived:
            sentences.append(
                f"The population-weighted mean for {scope} is {value}, computed "
                f"from {_join(measure_vars)}. NHIS publishes no single variable "
                f"for this, so it is derived with the formula shown in the query."
            )
        elif metric_kind == "mean":
            what = _join(measure_vars) if measure_vars else "the measure"
            sentences.append(
                f"The population-weighted mean of {what} for {scope} is {value}."
            )
        elif metric_kind == "percent" and target is not None:
            sentences.append(
                f"An estimated {value} of {scope} are classified as "
                f"\"{target[1]}\" on {_describe(target[0])}."
            )
        elif metric_kind == "percent":
            sentences.append(f"The estimated share of {scope} is {value}.")
        elif metric_kind == "count":
            sentences.append(f"The estimated number of adults is {value}.")
        else:
            sentences.append(f"The result for {scope} is {value}.")
    else:
        sentences.append(
            f"The query returned {len(shown_rows)} row(s) for {scope}; see the "
            f"table below."
        )

    excluded: list[str] = []
    for hit in used:
        for vl in hit.sentinel_labels:
            text = vl.label.strip()
            if text and text not in excluded:
                excluded.append(text)
    if excluded:
        sentences.append(
            "Excluded as missing: " + ", ".join(excluded[:_MAX_EXCLUSION_LABELS]) + "."
        )

    focus = group_vars[0] if group_vars else (measure_vars[0] if measure_vars else None)
    universe = (focus.universe_description or "").strip() if focus else ""
    if universe and universe.lower().rstrip(".") != _GENERIC_UNIVERSE:
        sentences.append(f"This was asked of: {universe}.")

    sentences.append(
        "Estimates use the NHIS final annual sample-adult weight (WTFA_A), so "
        "they describe the U.S. adult population rather than only the people "
        "surveyed."
    )
    return " ".join(sentences)
