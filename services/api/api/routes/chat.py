"""POST /api/chat — SSE orchestration entry point (Requirement 1.x, 15.1).

Streaming is not cosmetic here. A single question makes several sequential
model calls (rewrite, route, judge, generate); without streaming the
researcher watches a spinner for seconds with no indication of progress. The
event sequence also lets the UI show *how* the answer was reached — which
variables were retrieved, what the presence verdict was — rather than only
the prose.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from services.api.domain import SURVEY_YEARS_MAX, SURVEY_YEARS_MIN

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    year: int | None = Field(default=None, ge=SURVEY_YEARS_MIN, le=SURVEY_YEARS_MAX)


@router.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> EventSourceResponse:
    orchestrator = request.app.state.orchestrator

    async def event_stream():
        try:
            async for event in orchestrator.handle_question(
                payload.question,
                conversation_id=payload.conversation_id,
                survey_year=payload.year,
            ):
                yield {"data": json.dumps(event)}
        except Exception:  # noqa: BLE001
            # The orchestrator already converts failures into error events;
            # this only catches a failure of the generator itself, and must
            # still terminate the stream so the client does not hang.
            logger.exception("chat stream failed")
            yield {
                "data": json.dumps(
                    {
                        "type": "error",
                        "message": "The response stream failed unexpectedly.",
                    }
                )
            }
            yield {"data": json.dumps({"type": "done", "message_id": None})}

    return EventSourceResponse(event_stream())
