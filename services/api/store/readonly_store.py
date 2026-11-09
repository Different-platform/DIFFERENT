"""Execute generated SQL under the read-only role (Requirement 4.4, 7.2).

**SAFETY-CRITICAL.** This is the only place LLM-generated SQL is executed, and
it is executed with the narrowest privileges the database can give it. Two
independent guards wrap every statement:

1. `SET ROLE differ_ro` — the session drops to a role with SELECT-only grants
   (migration 003). A write or DDL statement is rejected by Postgres itself,
   regardless of what the application believes about it.
2. `SET TRANSACTION READ ONLY` — even a SELECT with a writing side effect (a
   data-modifying CTE, a volatile function) is refused.

This runs *after* `verify.check()` has already returned no problems. It is not
a substitute for verification — it is the backstop for the case where the
verifier is wrong. The product never relies on a single gate.

A statement timeout bounds a pathological query (an accidental cross join over
30k rows) so one researcher's question cannot stall the pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

# A generated analytical query should return quickly; anything longer is a
# mistake we would rather surface than wait on.
STATEMENT_TIMEOUT_MS = 15_000

# Never stream an unbounded result back into a chat response.
MAX_ROWS = 500

READONLY_ROLE = "differ_ro"


@dataclass(frozen=True)
class SqlResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False


class ReadOnlyExecutionError(RuntimeError):
    """Raised when generated SQL fails to execute under differ_ro.

    The orchestrator converts this into the same "no number" outcome a
    verification failure produces — a query that errors at execution must
    never yield a result, just as one that fails verification must not.
    """


def execute(conn: psycopg.Connection, sql: str) -> SqlResult:
    """Run `sql` as differ_ro in a read-only transaction and return its rows.

    `conn` is a normal application connection; this function switches it into
    the restricted role for the duration of one transaction and switches back,
    so the caller's pooled connection is never left with elevated-then-dropped
    privileges.
    """
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                cur.execute(f'SET LOCAL ROLE "{READONLY_ROLE}"')
                cur.execute("SET TRANSACTION READ ONLY")

                cur.execute(sql)  # type: ignore[arg-type]
                if cur.description is None:
                    raise ReadOnlyExecutionError(
                        "The generated query did not return a result set."
                    )
                columns = [desc.name for desc in cur.description]
                raw = cur.fetchmany(MAX_ROWS + 1)
                truncated = len(raw) > MAX_ROWS
                rows = [list(row) for row in raw[:MAX_ROWS]]
    except ReadOnlyExecutionError:
        raise
    except psycopg.Error as exc:
        # Permission denied, read-only violation, timeout, syntax — all land
        # here and all mean the same thing to the caller: no number.
        raise ReadOnlyExecutionError(str(exc).strip()) from exc

    return SqlResult(columns=columns, rows=_normalize(rows), truncated=truncated)


def _normalize(rows: list[list[Any]]) -> list[list[Any]]:
    """Render DB rows predictably for tests and logging.

    Values pass through unchanged; the events layer's _json_safe handles
    Decimal precisely, and every other type the driver returns (int, float,
    str, bool, None) is already JSON-safe.
    """
    return [list(row) for row in rows]
