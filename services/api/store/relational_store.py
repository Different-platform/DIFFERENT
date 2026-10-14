"""Conversation history and telemetry writes (Requirements 17.1, 20.1, 20.2).

Uses the application's read/write role. Generated SQL never runs through this
pool — that will be `readonly_store`'s job, bound to a SELECT-only role, and
keeping the two separate is the defense-in-depth boundary described in
design.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from services.api.domain import Citation
from services.api.llm.gateway import Message

logger = logging.getLogger(__name__)


def ensure_conversation(
    conn: psycopg.Connection,
    conversation_id: UUID | str | None,
    *,
    default_year: int | None = None,
    title: str | None = None,
) -> UUID:
    """Return an existing conversation id, or create one.

    `user_id` is left NULL: no login exists in this version (Req 20.2) and the
    column is reserved for a future accounts table (Req 20.1).
    """
    with conn.cursor() as cur:
        if conversation_id is not None:
            cur.execute(
                "SELECT id FROM conversations WHERE id = %s", (str(conversation_id),)
            )
            row = cur.fetchone()
            if row is not None:
                return row[0]

        cur.execute(
            """
            INSERT INTO conversations (id, user_id, title, default_year)
            VALUES (COALESCE(%s::uuid, gen_random_uuid()), NULL, %s, %s)
            RETURNING id
            """,
            (str(conversation_id) if conversation_id else None, title, default_year),
        )
        return cur.fetchone()[0]


def load_history(
    conn: psycopg.Connection, conversation_id: UUID | str, *, limit: int = 12
) -> list[Message]:
    """Recent turns, oldest first, for follow-up rewriting."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT role, content
            FROM (
                SELECT role, content, created_at, id
                FROM messages
                WHERE conversation_id = %s AND content <> ''
                ORDER BY created_at DESC, id DESC
                LIMIT %s
            ) recent
            ORDER BY created_at ASC, id ASC
            """,
            (str(conversation_id), limit),
        )
        return [Message(role=row[0], content=row[1]) for row in cur.fetchall()]


def insert_message(
    conn: psycopg.Connection,
    conversation_id: UUID | str,
    *,
    role: str,
    content: str,
    citations: list[Citation] | None = None,
    verdict: str | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO messages
                (conversation_id, role, content, citations, verdict)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
                str(conversation_id),
                role,
                content,
                json.dumps([c.to_dict() for c in citations]) if citations else None,
                verdict,
            ),
        )
        return cur.fetchone()[0]


def update_message(
    conn: psycopg.Connection,
    message_id: int,
    *,
    content: str,
    citations: list[Citation] | None = None,
    verdict: str | None = None,
) -> None:
    """Fill in an assistant turn once streaming has finished."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE messages
            SET content = %s,
                citations = COALESCE(%s::jsonb, citations),
                verdict = COALESCE(%s, verdict)
            WHERE id = %s
            """,
            (
                content,
                json.dumps([c.to_dict() for c in citations]) if citations else None,
                verdict,
                message_id,
            ),
        )


def write_query_log(
    conn: psycopg.Connection,
    *,
    question: str,
    conversation_id: UUID | str | None = None,
    message_id: int | None = None,
    rewritten_question: str | None = None,
    intent: str | None = None,
    verdict: str | None = None,
    top_variable: str | None = None,
    top_similarity: float | None = None,
    retrieved: list[dict[str, Any]] | None = None,
    generated_sql: str | None = None,
    sql_verified: bool | None = None,
    verification_notes: Any = None,
    error: str | None = None,
    latency_ms: int | None = None,
) -> int:
    """Record one assistant turn (Requirement 17.1).

    Written for failed turns too, with `error` populated and `verdict` NULL.
    A turn that vanishes from telemetry because it errored is a turn nobody
    can debug, and it quietly biases any latency measurement.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO query_log (
                message_id, conversation_id, question, rewritten_question,
                intent, verdict, top_variable, top_similarity, retrieved,
                generated_sql, sql_verified, verification_notes, error,
                latency_ms
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s::jsonb,
                %s, %s, %s::jsonb, %s,
                %s
            )
            RETURNING id
            """,
            (
                message_id,
                str(conversation_id) if conversation_id else None,
                question,
                rewritten_question,
                intent,
                verdict,
                top_variable,
                top_similarity,
                json.dumps(retrieved) if retrieved is not None else None,
                generated_sql,
                sql_verified,
                json.dumps(verification_notes)
                if verification_notes is not None
                else None,
                error,
                latency_ms,
            ),
        )
        return cur.fetchone()[0]


def list_conversations(conn: psycopg.Connection, *, limit: int = 50) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT c.id, c.title, c.default_year, c.created_at,
                   count(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
