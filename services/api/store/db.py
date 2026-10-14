"""Connection pooling and async bridging for synchronous psycopg.

The pipeline is async (SSE streaming, concurrent requests) while psycopg and
sentence-transformers are synchronous. Database work therefore runs in worker
threads via `run_db`.

Connections are acquired per operation rather than held for the lifetime of a
request. A single question makes several LLM calls taking seconds; holding a
pooled connection across those awaits would let 30-40 concurrent researchers
exhaust the pool while almost every connection sat idle waiting on Groq
(Requirement 19).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

import psycopg
from psycopg_pool import ConnectionPool

from services.api.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_pool: ConnectionPool | None = None


def open_pool(min_size: int = 2, max_size: int = 10) -> ConnectionPool:
    """Create the shared pool. Called once at application startup."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=min_size,
            max_size=max_size,
            open=True,
            timeout=10.0,
        )
        logger.info("database pool opened (min=%s max=%s)", min_size, max_size)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("database pool closed")


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("database pool is not open; call open_pool() at startup")
    return _pool


async def run_db(
    operation: Callable[..., T], *args: Any, **kwargs: Any
) -> T:
    """Run a synchronous store function against a pooled connection.

    `operation` must accept a `psycopg.Connection` as its first argument.
    """

    def _work() -> T:
        with get_pool().connection() as conn:
            return operation(conn, *args, **kwargs)

    return await asyncio.to_thread(_work)


def database_reachable() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False
