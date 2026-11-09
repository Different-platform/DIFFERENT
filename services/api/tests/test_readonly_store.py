"""Read-only execution boundary tests (Requirement 7.2, 7.3).

SAFETY-CRITICAL, and an integration test by necessity: whether Postgres
rejects a write under `differ_ro` is database behaviour, not application logic,
so it must run against a real instance. Skipped automatically when no database
is reachable (see the `integration` marker in pytest.ini).
"""

from __future__ import annotations

import psycopg
import pytest

from services.api.config import settings
from services.api.store import readonly_store


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_reachable(), reason="no database reachable"),
]


@pytest.fixture
def conn():
    connection = psycopg.connect(settings.database_url)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_select_succeeds_under_readonly(conn):
    result = readonly_store.execute(conn, "SELECT count(*) AS n FROM codebook_variables")
    assert result.columns == ["n"]
    assert result.rows and isinstance(result.rows[0][0], int)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO sections (survey_year, code, name) VALUES (1901, 'X', 'Y')",
        "UPDATE sections SET name = name",
        "DELETE FROM sections",
        "DROP TABLE sections",
        "CREATE TABLE evil (id int)",
        "ALTER TABLE sections ADD COLUMN evil int",
    ],
)
def test_write_and_ddl_statements_are_rejected(conn, statement):
    """Postgres itself refuses the statement; no row is ever written."""
    with pytest.raises(readonly_store.ReadOnlyExecutionError):
        readonly_store.execute(conn, statement)


def test_connection_is_restored_to_read_write_after_execute(conn):
    """The restricted role is scoped to the transaction, not leaked onward."""
    readonly_store.execute(conn, "SELECT 1")
    with conn.cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone()[0] != "differ_ro"
    conn.rollback()
