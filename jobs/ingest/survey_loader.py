"""Load NHIS Sample Adult survey microdata into a per-year table.

Separate from the codebook loader: the codebook (variable descriptions, value
labels, sentinels) is what retrieval and the judge use; this is the microdata
the verified-statistics path actually computes over.

Design decisions:

- **One table per year** (`survey_YYYY`), columns generated from the CSV
  header rather than hand-written, because each year has a different ~600-640
  column set (Requirement 10.1-10.3). The column names are the NHIS variable
  names lower-cased, which is exactly what `sql_gen`/`verify` expect.
- **Column types are inferred from the data.** Most columns are `numeric`
  (codes and continuous measures), and `numeric` holds both integer codes and
  fractional sentinels (BMI 99.9) without the truncation an `int` would cause.
  A few identifier columns (e.g. HHX = 'H029691') are alphanumeric and are
  typed `text`; a numeric-only assumption fails the COPY on the first row.
- **Bulk load via COPY**, in one transaction per year. Either the whole year
  lands or none of it does, mirroring the codebook loader's all-or-nothing
  write.

Idempotent: re-loading a year drops and recreates its table.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import psycopg


@dataclass
class SurveyLoadSummary:
    survey_year: int
    columns: int
    rows: int

    def __str__(self) -> str:
        return (
            f"loaded survey_{self.survey_year}: {self.rows} rows, "
            f"{self.columns} columns"
        )


def _sanitize(name: str) -> str:
    """Lower-case and make a CSV header safe as a Postgres identifier."""
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name.strip())
    if not cleaned:
        cleaned = "col"
    if cleaned[0].isdigit():
        cleaned = f"c_{cleaned}"
    return cleaned.lower()


def read_header(csv_path: Path) -> list[str]:
    with csv_path.open("r", newline="") as fh:
        reader = csv.reader(fh)
        raw = next(reader)
    return [_sanitize(col) for col in raw]


def _is_numeric(token: str) -> bool:
    if token == "":
        return True  # empty -> NULL, compatible with numeric
    try:
        float(token)
        return True
    except ValueError:
        return False


def infer_column_types(csv_path: Path, sample_rows: int = 5000) -> list[str]:
    """Type each column as `numeric` or `text` by sampling the data.

    Most NHIS columns are numeric codes, but identifier columns (e.g. HHX, the
    household id) carry alphanumeric values like 'H029691'. A column is
    `numeric` only if every sampled non-empty value parses as a number;
    otherwise it is `text`. Sampling bounds the scan while reliably catching
    the handful of id columns, which are non-numeric in their very first row.
    """
    with csv_path.open("r", newline="") as fh:
        reader = csv.reader(fh)
        next(reader)  # header
        numeric: list[bool] | None = None
        for i, row in enumerate(reader):
            if i >= sample_rows:
                break
            if numeric is None:
                numeric = [True] * len(row)
            for j, value in enumerate(row):
                if j < len(numeric) and numeric[j] and not _is_numeric(value):
                    numeric[j] = False
    if numeric is None:
        numeric = []
    return ["numeric" if flag else "text" for flag in numeric]


def load_survey_year(
    conn: psycopg.Connection, survey_year: int, csv_path: Path
) -> SurveyLoadSummary:
    """Create `survey_<year>` from the CSV header and COPY every row in."""
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    columns = read_header(csv_path)
    if not columns:
        raise ValueError(f"{csv_path} has no header row")

    types = infer_column_types(csv_path)
    if len(types) != len(columns):
        # Header and data row widths disagree; pad toward `text` so the load
        # does not fail on a column the sampler never saw.
        types = (types + ["text"] * len(columns))[: len(columns)]

    table = f"survey_{survey_year}"
    # Column types inferred from the data; identifiers are sanitized and quoted.
    col_defs = ",\n    ".join(
        f'"{col}" {col_type}' for col, col_type in zip(columns, types, strict=True)
    )
    quoted_cols = ", ".join(f'"{col}"' for col in columns)

    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{table}"')
        cur.execute(f'CREATE TABLE "{table}" (\n    {col_defs}\n)')

        copy_sql = (
            f'COPY "{table}" ({quoted_cols}) FROM STDIN '
            f"WITH (FORMAT csv, HEADER true, NULL '')"
        )
        with cur.copy(copy_sql) as copy, csv_path.open("rb") as fh:
            while chunk := fh.read(1 << 20):  # 1 MiB chunks
                copy.write(chunk)

        # Grant SELECT to the read-only role for this freshly created table.
        # (ALTER DEFAULT PRIVILEGES covers future tables, but this table is
        # created by the app role, so grant explicitly to be certain.)
        cur.execute(f'GRANT SELECT ON "{table}" TO differ_ro')

        cur.execute(f'SELECT count(*) FROM "{table}"')
        row_count = cur.fetchone()[0]

    return SurveyLoadSummary(
        survey_year=survey_year, columns=len(columns), rows=row_count
    )


def survey_csv_path(nhis_data_dir: Path, survey_year: int) -> Path:
    """Locate a 2019+ year's Sample Adult CSV inside NHIS_DATA_DIR."""
    short = str(survey_year)[2:]  # 2023 -> '23'
    return nhis_data_dir / f"NHIS_adult_{survey_year}" / f"adult{short}.csv"
