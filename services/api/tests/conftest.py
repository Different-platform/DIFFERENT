"""Shared test fixtures.

Integration tests that need Postgres skip themselves cleanly when no
database is reachable, so the unit suite still runs on a machine with no
container runtime.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from services.api.config import settings


def _database_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


DB_AVAILABLE = _database_reachable()

requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason=f"no Postgres reachable at {settings.database_url}",
)


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection]:
    """A connection that rolls back everything the test did."""
    conn = psycopg.connect(settings.database_url)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
