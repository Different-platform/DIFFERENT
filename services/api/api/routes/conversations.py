"""GET /api/conversations, GET /api/conversations/{id}/messages.

Reload a researcher's earlier threads after a page refresh. The data has
always been there — every turn is written to `conversations`/`messages`
regardless of whether anything ever reads it back — this just exposes the
read side the UI needs to populate its sidebar instead of starting blank
every time the page loads.

No auth: there is no login in this version (Requirement 20.2), so this is
every conversation anyone has ever had with this deployment, visible to
anyone who can reach the API. Acceptable for the current single-shared-URL
deployment model; would need to be scoped by user_id the moment accounts
exist.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from services.api.store import db, relational_store

router = APIRouter(tags=["conversations"])


@router.get("/api/conversations")
async def list_conversations(limit: int = 50) -> dict:
    limit = max(1, min(limit, 200))
    rows = await db.run_db(relational_store.list_conversations, limit=limit)
    return {
        "conversations": [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "default_year": row["default_year"],
                "created_at": row["created_at"].isoformat(),
                "message_count": row["message_count"],
            }
            for row in rows
            # A conversation created but never asked anything (e.g. "New
            # conversation" clicked and abandoned) is not a thread worth
            # showing in history.
            if row["message_count"] > 0
        ]
    }


@router.get("/api/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: UUID) -> dict:
    rows = await db.run_db(relational_store.list_messages, conversation_id)
    if not rows:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "messages": [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "citations": row["citations"] or [],
                "verdict": row["verdict"],
                "sql": (
                    {
                        "sql": row["generated_sql"],
                        "verified": bool(row["sql_verified"]),
                        "problems": [],
                    }
                    if row["generated_sql"]
                    else None
                ),
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]
    }
