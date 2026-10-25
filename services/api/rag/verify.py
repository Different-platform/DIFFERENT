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


def check(sql: str, codebook: Codebook) -> list[Problem]:
    """Return every reason `sql` must not run; empty means verified.

    `codebook` maps lower-cased survey column name -> the set of numeric codes
    the codebook defines for that variable in the relevant survey year. A
    column absent from the mapping is not checked for codes (the generator may
    legitimately reference a continuous measure or a design variable), but the
    statement-shape guards still apply to the whole query.
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
