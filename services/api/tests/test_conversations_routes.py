"""GET /api/conversations and GET /api/conversations/{id}/messages.

Direct calls to the route functions with db.run_db monkeypatched, rather
than a full TestClient against the real app — the real app's lifespan opens
an actual database pool and loads the embedding model, neither of which
these pure read-and-shape routes need to exercise.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from services.api.api.routes import conversations
from services.api.store import db, relational_store


async def test_list_conversations_shapes_rows_for_the_sidebar(monkeypatch):
    cid = uuid4()
    rows = [
        {
            "id": cid,
            "title": "What is the average BMI?",
            "default_year": 2023,
            "created_at": datetime(2026, 1, 1, 12, 0, 0),
            "message_count": 4,
        }
    ]

    async def fake_run_db(operation, *args, **kwargs):
        assert operation is relational_store.list_conversations
        assert kwargs == {"limit": 50}
        return rows

    monkeypatch.setattr(db, "run_db", fake_run_db)

    payload = await conversations.list_conversations(limit=50)
    assert payload == {
        "conversations": [
            {
                "id": str(cid),
                "title": "What is the average BMI?",
                "default_year": 2023,
                "created_at": "2026-01-01T12:00:00",
                "message_count": 4,
            }
        ]
    }


async def test_list_conversations_drops_empty_threads(monkeypatch):
    """A conversation row created by clicking "New conversation" and never
    asking anything is not a thread worth showing in history."""
    rows = [
        {
            "id": uuid4(),
            "title": "New conversation",
            "default_year": None,
            "created_at": datetime(2026, 1, 1),
            "message_count": 0,
        }
    ]

    async def fake_run_db(operation, *args, **kwargs):
        del operation, args, kwargs
        return rows

    monkeypatch.setattr(db, "run_db", fake_run_db)

    payload = await conversations.list_conversations(limit=50)
    assert payload == {"conversations": []}


async def test_list_conversations_clamps_limit(monkeypatch):
    seen = {}

    async def fake_run_db(operation, *args, **kwargs):
        del operation, args
        seen.update(kwargs)
        return []

    monkeypatch.setattr(db, "run_db", fake_run_db)

    await conversations.list_conversations(limit=10_000)
    assert seen["limit"] == 200
    await conversations.list_conversations(limit=0)
    assert seen["limit"] == 1


async def test_get_messages_includes_sql_only_when_one_ran(monkeypatch):
    rows = [
        {
            "id": 1,
            "role": "user",
            "content": "What is the average BMI in 2023?",
            "citations": None,
            "verdict": None,
            "generated_sql": None,
            "sql_verified": None,
            "created_at": datetime(2026, 1, 1, 12, 0, 0),
        },
        {
            "id": 2,
            "role": "assistant",
            "content": "28.03",
            "citations": [
                {"variable_name": "BMICAT_A", "section": "BMI", "page": 1, "survey_year": 2023}
            ],
            "verdict": "MEASURED",
            "generated_sql": "SELECT ...",
            "sql_verified": True,
            "created_at": datetime(2026, 1, 1, 12, 0, 5),
        },
    ]

    async def fake_run_db(operation, *args, **kwargs):
        assert operation is relational_store.list_messages
        del args, kwargs
        return rows

    monkeypatch.setattr(db, "run_db", fake_run_db)

    payload = await conversations.get_messages(conversation_id=UUID(int=1))
    messages = payload["messages"]
    assert messages[0]["sql"] is None
    assert messages[1]["sql"] == {"sql": "SELECT ...", "verified": True, "problems": []}
    assert messages[1]["citations"][0]["variable_name"] == "BMICAT_A"


async def test_get_messages_404s_for_an_unknown_conversation(monkeypatch):
    async def fake_run_db(operation, *args, **kwargs):
        del operation, args, kwargs
        return []

    monkeypatch.setattr(db, "run_db", fake_run_db)

    with pytest.raises(HTTPException) as exc_info:
        await conversations.get_messages(conversation_id=UUID(int=1))
    assert exc_info.value.status_code == 404
