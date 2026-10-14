"""Apply numbered SQL migrations in order, once each.

Deliberately not Alembic: the schema is small, the team is two people, and
plain SQL files are easier to review than generated Python. Applied files are
recorded in schema_migrations so re-running is a no-op.

Usage, from the repo root:
    .venv/bin/python db/run_migrations.py          # apply pending
    .venv/bin/python db/run_migrations.py --check  # report status only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.api.config import settings  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def discover_migrations() -> list[Path]:
    """Return migration files in lexicographic (= numeric prefix) order."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied_filenames(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(CREATE_TRACKING_TABLE)
        cur.execute("SELECT filename FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}
    # Note: committed by the caller's transaction context.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report which migrations are pending without applying them",
    )
    args = parser.parse_args()

    migrations = discover_migrations()
    if not migrations:
        print(f"No .sql files found in {MIGRATIONS_DIR}")
        return 1

    try:
        conn = psycopg.connect(settings.database_url, connect_timeout=5)
    except psycopg.OperationalError as exc:
        print("Could not connect to Postgres.", file=sys.stderr)
        print(f"  DATABASE_URL: {settings.database_url}", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        print(
            "\nStart a local database with `docker compose up -d`, or point "
            "DATABASE_URL at Supabase.",
            file=sys.stderr,
        )
        return 2

    with conn:
        already = applied_filenames(conn)
        pending = [m for m in migrations if m.name not in already]

        if args.check:
            for m in migrations:
                mark = "applied" if m.name in already else "PENDING"
                print(f"  [{mark:>7}] {m.name}")
            return 0

        if not pending:
            print(f"Up to date — {len(already)} migration(s) already applied.")
            return 0

        for migration in pending:
            sql = migration.read_text()
            print(f"Applying {migration.name} ...", end=" ", flush=True)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)",
                    (migration.name,),
                )
            print("ok")

    print(f"Applied {len(pending)} migration(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
