"""Health and readiness.

Reports whether the LLM is real or stubbed. That distinction must be visible
somewhere unambiguous: a stub answer reads like a real one, and nobody should
be able to demo stubbed judging believing it is model output.
"""

from __future__ import annotations

from fastapi import APIRouter

from services.api.config import settings
from services.api.store import db, vector_store

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health() -> dict:
    reachable = await db.run_db_safe()
    years: list[dict] = []
    if reachable:
        try:
            years = await db.run_db(vector_store.available_years)
        except Exception:  # noqa: BLE001
            years = []

    return {
        "status": "ok" if reachable else "degraded",
        "database": "up" if reachable else "unreachable",
        "llm": "groq" if settings.has_real_llm else "stub",
        "llm_model": settings.groq_model if settings.has_real_llm else "stub",
        "embedding_model": settings.embedding_model,
        "ingested_years": years,
    }
