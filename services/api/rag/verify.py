"""SQL verification against stored value labels (Requirement 6.1, 6.2, 6.3).

**SAFETY-CRITICAL.** This is the second of the two safety gates and the one
the product promise rests on: a numeric result must never reach a researcher
unless `check()` returned an empty problem list for the exact SQL about to be
executed.

The failure this prevents is concrete. Given thin context, the model invents
missing-data codes — it wrote `NOT IN (77, 88, 99)` when the real sentinels on
the variable were `7, 8, 9`, and returned a confident, plausible-looking
percentage computed from the wrong rows. A number that looks right and is
wrong is worse than no number, because a researcher cannot tell the difference.

`check()` is a **pure function**: `(sql, codebook) -> list[Problem]`. It takes
the codebook codes it checks against as an argument rather than reaching for a
database, so the same function backs both production and the property test, and
the test needs no Postgres. The orchestrator loads the real value labels (from
the retrieved hits) and passes them in.

What it verifies, and deliberately does not:

- **Does** check that every numeric literal compared against a known codebook
  column is a defined value label (substantive or sentinel) for that column in
  that survey year. A code the codebook never defines is a `Problem`.
- **Does** reject any statement that is not a single read-only SELECT. Write
  or schema-modifying verbs, multiple statements, and comment-smuggled tails
  are problems in their own right — the `differ_ro` role is the enforcing
  gate, but catching it here means the researcher sees *why* rather than a
  raw Postgres permission error.
- **Does not** attempt to validate statistical correctness, join logic, or
  whether the question was answered well. It validates codes, not analysis.
  Erring toward flagging (a false positive costs a retry; a false negative
  ships a wrong number) is the intended bias.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# A codebook, as this module needs it: per column (lower-cased survey column
# name), the set of numeric codes the codebook defines for that variable in a
# given year. Both substantive and sentinel codes are "defined"; the point of
# verification is that the SQL only references codes that exist at all.
Codebook = Mapping[str, frozenset[Decimal]]


@dataclass(frozen=True)
class Problem:
    """One reason a generated query must not be executed.

    `kind` is stable and machine-readable; `message` is the researcher-facing
    sentence shown next to the (unexecuted, `verified: false`) SQL.
    """

    kind: str
    message: str
    column: str | None = None
    code: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "message": self.message,
            "column": self.column,
            "code": self.code,
        }


# --- statement-shape guards -------------------------------------------------

# Verbs that must never appear: anything that writes or changes schema. Matched
# as whole words, case-insensitively. This is belt-and-braces with the
# differ_ro role, which rejects them at the database; catching them here turns
# a permission error into an explainable verification failure.
_FORBIDDEN_VERBS = (
    "insert",
    "update",
    "delete",
    "drop",
    "truncate",
    "alter",
    "create",
    "grant",
    "revoke",
    "merge",
    "copy",
    "call",
    "do",
    "vacuum",
    "comment",
)

_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_VERBS) + r")\b", re.IGNORECASE
)

# Numeric literal compared against a column: `COLUMN <op> 123` or the reverse,
# and the list form `COLUMN IN (1, 2, 3)` / `NOT IN (...)`. These two patterns
# are where generated SQL references value codes, and therefore where an
# invented code hides. Column names are NHIS-style identifiers.
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_NUM = r"-?\d+(?:\.\d+)?"

_COMPARISON_RE = re.compile(
    rf"\b(?P<col>{_IDENT})\s*(?:=|!=|<>|>=|<=|>|<)\s*(?P<code>{_NUM})",
)
_COMPARISON_REV_RE = re.compile(
    rf"(?P<code>{_NUM})\s*(?:=|!=|<>|>=|<=|>|<)\s*(?P<col>{_IDENT})\b",
)
_IN_RE = re.compile(
    rf"\b(?P<col>{_IDENT})\s+(?:not\s+)?in\s*\((?P<codes>[^)]*)\)",
    re.IGNORECASE,
)
_BETWEEN_RE = re.compile(
    rf"\b(?P<col>{_IDENT})\s+between\s+(?P<lo>{_NUM})\s+and\s+(?P<hi>{_NUM})",
    re.IGNORECASE,
)

# Identifiers that look like columns in a comparison but are structural design
# variables, not coded survey answers. Comparing against them (e.g. filtering a
# weight or a year) references a continuous/id value, not a value label, so a
# literal there is never a "code" to verify.
_UNCODED_COLUMNS: frozenset[str] = frozenset(
    {
        "wtfa_a",
        "wtfa_sa",
        "pstrat",
        "ppsu",
        "survey_year",
        "srvy_yr",
        "respondent_id",
        "limit",
        "offset",
    }
)


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _strip_sql(sql: str) -> str:
    """Remove string literals and comments so they cannot smuggle tokens.

    A quoted string could contain the word "DELETE" or a bare number that is
    not a code; stripping them keeps both the forbidden-verb scan and the
    code scan honest.
    """
    without_line = re.sub(r"--[^\n]*", " ", sql)
    without_block = re.sub(r"/\*.*?\*/", " ", without_line, flags=re.DOTALL)
    without_strings = re.sub(r"'(?:[^']|'')*'", " '' ", without_block)
    return without_strings


def _split_statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def check(
    sql: str, codebook: Codebook, sentinels: Codebook | None = None
) -> list[Problem]:
    """Return every reason `sql` must not run; empty means verified.

    `codebook` maps lower-cased survey column name -> the set of numeric codes
    the codebook defines for that variable in the relevant survey year. A
    column absent from the mapping is not checked for codes (the generator may
    legitimately reference a continuous measure or a design variable), but the
    statement-shape guards still apply to the whole query.

    `sentinels` maps the same column names to just their missing-data codes.
    When given, every column the query uses must have those codes excluded.
    Checking that codes *exist* is not enough: a mean of raw weights that still
    contains 999 ("Don't know") passes the existence check and is silently
    wrong. This matters most for derived measures such as BMI from height and
    weight, where one unexcluded sentinel distorts the whole average.
    """
    problems: list[Problem] = []
    cleaned = _strip_sql(sql)

    if not cleaned.strip():
        return [Problem("empty", "The generated query was empty.")]

    # --- statement shape ---
    statements = _split_statements(cleaned)
    if len(statements) > 1:
        problems.append(
            Problem(
                "multiple_statements",
                "The generated query contains more than one statement; only a "
                "single read-only SELECT is allowed.",
            )
        )

    for verb in _FORBIDDEN_RE.findall(cleaned):
        problems.append(
            Problem(
                "forbidden_statement",
                f"The generated query contains a '{verb.upper()}' statement, "
                f"which is not permitted; only read-only SELECTs may run.",
            )
        )

    first = statements[0].lstrip().lower() if statements else ""
    if first and not (first.startswith("select") or first.startswith("with")):
        problems.append(
            Problem(
                "not_a_select",
                "The generated query is not a SELECT statement.",
            )
        )

    # --- code references ---
    normalized_codebook = {col.lower(): codes for col, codes in codebook.items()}

    def check_code(column: str, raw_code: str) -> None:
        col = column.lower()
        if col in _UNCODED_COLUMNS or col not in normalized_codebook:
            return
        value = _to_decimal(raw_code)
        if value is None:
            return
        if value not in normalized_codebook[col]:
            defined = ", ".join(
                str(_render(code)) for code in sorted(normalized_codebook[col])
            )
            problems.append(
                Problem(
                    "unknown_code",
                    f"Code {_render(value)} is not a defined value for "
                    f"{column} in this survey year. Defined codes are: "
                    f"{defined or '(none)'}.",
                    column=column,
                    code=str(_render(value)),
                )
            )

    for match in _COMPARISON_RE.finditer(cleaned):
        check_code(match.group("col"), match.group("code"))
    for match in _COMPARISON_REV_RE.finditer(cleaned):
        check_code(match.group("col"), match.group("code"))
    for match in _IN_RE.finditer(cleaned):
        column = match.group("col")
        for raw in match.group("codes").split(","):
            token = raw.strip()
            if re.fullmatch(_NUM, token):
                check_code(column, token)
    for match in _BETWEEN_RE.finditer(cleaned):
        check_code(match.group("col"), match.group("lo"))
        check_code(match.group("col"), match.group("hi"))

    if sentinels:
        problems.extend(_sentinel_problems(cleaned, sentinels))

    return problems


# --- missing-data exclusion -------------------------------------------------

_CASE_RE = re.compile(r"\bcase\b.*?\bend\b", re.IGNORECASE | re.DOTALL)


def _decimals(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in text.split(","):
        token = token.strip()
        if re.fullmatch(_NUM, token):
            value = _to_decimal(token)
            if value is not None:
                found.add(value)
    return found


def _excludes_sentinels(text: str, column: str, codes: frozenset[Decimal]) -> bool:
    """Whether `text` keeps every missing-data code of `column` out of the result.

    Two ways to do it: list the codes in a NOT IN / != predicate, or admit only
    substantive values with a positive predicate (`= 4`, `IN (1, 2)`, or a
    BETWEEN range containing no missing-data code). `text` has CASE expressions
    removed: `SUM(CASE WHEN c = 4 ...)` picks a numerator but filters no rows,
    so it must not count as excluding anything.
    """
    col = re.escape(column)
    flags = re.IGNORECASE

    excluded: set[Decimal] = set()
    for m in re.finditer(rf"\b{col}\s+not\s+in\s*\(([^)]*)\)", text, flags):
        excluded |= _decimals(m.group(1))
    for m in re.finditer(rf"\b{col}\s*(?:!=|<>)\s*({_NUM})", text, flags):
        excluded |= _decimals(m.group(1))
    for m in re.finditer(rf"({_NUM})\s*(?:!=|<>)\s*{col}\b", text, flags):
        excluded |= _decimals(m.group(1))
    if codes <= excluded:
        return True

    for m in re.finditer(rf"\b{col}\s+in\s*\(([^)]*)\)", text, flags):
        admitted = _decimals(m.group(1))
        if admitted and not (admitted & codes):
            return True
    for pattern in (rf"\b{col}\s*=\s*({_NUM})", rf"({_NUM})\s*=\s*{col}\b"):
        for m in re.finditer(pattern, text, flags):
            if _decimals(m.group(1)) - codes:
                return True
    for m in re.finditer(
        rf"\b{col}\s+between\s+({_NUM})\s+and\s+({_NUM})", text, flags
    ):
        low, high = _to_decimal(m.group(1)), _to_decimal(m.group(2))
        if low is not None and high is not None and not any(
            low <= code <= high for code in codes
        ):
            return True
    return False


def _sentinel_problems(cleaned: str, sentinels: Codebook) -> list[Problem]:
    without_case = _CASE_RE.sub(" ", cleaned)
    problems: list[Problem] = []
    for column, codes in sentinels.items():
        if not codes or column.lower() in _UNCODED_COLUMNS:
            continue
        if not re.search(rf"\b{re.escape(column)}\b", cleaned, re.IGNORECASE):
            continue
        if _excludes_sentinels(without_case, column, frozenset(codes)):
            continue
        listed = ", ".join(str(_render(code)) for code in sorted(codes))
        problems.append(
            Problem(
                "sentinel_not_excluded",
                f"{column.upper()} has missing-data codes ({listed}) that this "
                f"query does not exclude, so they would be counted as real "
                f"answers. Add {column.upper()} NOT IN ({listed}).",
                column=column.upper(),
            )
        )
    return problems


def _render(value: Decimal) -> int | Decimal:
    """Show an integer code without a trailing `.0`, keep fractional ones."""
    if value == value.to_integral_value():
        return int(value)
    return value


def codebook_from_hits(
    hits, survey_year: int | None = None
) -> dict[str, frozenset[Decimal]]:
    """Build the verifier's codebook from retrieved hits.

    Each hit's survey column is its variable name lower-cased (the loader names
    survey columns after codebook variable names), mapped to the set of every
    code — substantive and sentinel — the codebook defines for it. Only hits
    matching `survey_year` are included when one is given, so codes from a
    different year's edition of the same variable cannot leak in.
    """
    codebook: dict[str, frozenset[Decimal]] = {}
    for hit in hits:
        if survey_year is not None and hit.survey_year != survey_year:
            continue
        codes = frozenset(vl.code for vl in hit.value_labels)
        if codes:
            codebook[hit.variable_name.lower()] = codes
    return codebook


def sentinels_from_hits(
    hits, survey_year: int | None = None
) -> dict[str, frozenset[Decimal]]:
    """Each retrieved variable's missing-data codes, for `check(sentinels=...)`.

    Variables with no sentinel codes are omitted: there is nothing to exclude.
    Same year filter as `codebook_from_hits`, so a sentinel from another year's
    edition of a variable cannot leak in.
    """
    found: dict[str, frozenset[Decimal]] = {}
    for hit in hits:
        if survey_year is not None and hit.survey_year != survey_year:
            continue
        codes = frozenset(vl.code for vl in hit.sentinel_labels)
        if codes:
            found[hit.variable_name.lower()] = codes
    return found
