"""Offline ingestion entry point (Requirements 8.1, 8.2, 8.3, 9.x, 10.x).

Pipeline order, matching design.md: read -> parse -> abort guard -> embed ->
load. The guard runs before embedding so a failed parse costs no model time
and, critically, before any write so a failed year leaves nothing behind.

This job is the only component that reads source PDFs. The API never parses a
codebook at query time (Requirement 8.2).

Usage, from the repo root:
    .venv/bin/python -m jobs.ingest.run_ingest --year 2023
    .venv/bin/python -m jobs.ingest.run_ingest --year 2023 --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import psycopg

from jobs.ingest.abort_guard import check_abort
from jobs.ingest.embedder import Embedder
from jobs.ingest.loader import load_year
from jobs.ingest.parser_2019plus.codebook_parser import parse_codebook
from services.api.config import settings

# Years using the redesigned questionnaire, handled by parser_2019plus.
REDESIGNED_YEARS = (2019, 2020, 2021, 2022, 2023)


def codebook_path(survey_year: int) -> Path:
    """Locate a year's codebook PDF inside NHIS_DATA_DIR."""
    return (
        settings.nhis_data_dir
        / f"NHIS_adult_{survey_year}"
        / f"adult-codebook-{survey_year}.pdf"
    )


def ingest_year(survey_year: int, *, dry_run: bool = False) -> int:
    """Ingest one survey year. Returns a process exit code."""
    if survey_year not in REDESIGNED_YEARS:
        print(
            f"Year {survey_year} is not handled by the 2019+ parser. "
            f"Pre-2019 years need the legacy parser, which is not built yet.",
            file=sys.stderr,
        )
        return 2

    pdf = codebook_path(survey_year)
    if not pdf.exists():
        print(f"Codebook not found: {pdf}", file=sys.stderr)
        print(
            f"Check NHIS_DATA_DIR (currently {settings.nhis_data_dir}).",
            file=sys.stderr,
        )
        return 2

    print(f"[{survey_year}] parsing {pdf.name} ...", flush=True)
    started = time.time()
    variables = parse_codebook(pdf, survey_year)
    print(
        f"[{survey_year}] parsed {len(variables)} variables "
        f"in {time.time() - started:.1f}s"
    )

    # Abort guard — before embedding, before any write (Req 9.2).
    decision = check_abort(
        survey_year=survey_year,
        parsed_count=len(variables),
        threshold=settings.ingest_min_variables,
    )
    print(f"[{survey_year}] {decision.report}")
    if decision.should_abort:
        return 1

    labels = sum(len(v.value_labels) for v in variables)
    sentinels = sum(len(v.sentinel_labels) for v in variables)
    continuous = sum(1 for v in variables if v.is_continuous)
    print(
        f"[{survey_year}] {labels} value labels "
        f"({sentinels} sentinel), {continuous} continuous variables"
    )

    if dry_run:
        print(f"[{survey_year}] dry run — nothing written")
        return 0

    print(f"[{survey_year}] embedding with {settings.embedding_model} ...", flush=True)
    started = time.time()
    embedder = Embedder()

    try:
        with psycopg.connect(settings.database_url) as conn:
            summary = load_year(conn, survey_year, variables, embedder)
    except psycopg.OperationalError as exc:
        print(f"Could not connect to Postgres: {exc}", file=sys.stderr)
        print(
            "Start the database with `docker compose up -d`, or point "
            "DATABASE_URL at Supabase.",
            file=sys.stderr,
        )
        return 2

    print(f"[{survey_year}] {summary} in {time.time() - started:.1f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--year",
        type=int,
        action="append",
        dest="years",
        required=True,
        help="survey year to ingest; repeatable",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and run the guard, but do not embed or write",
    )
    args = parser.parse_args()

    worst = 0
    for survey_year in args.years:
        code = ingest_year(survey_year, dry_run=args.dry_run)
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
