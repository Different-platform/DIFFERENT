"""Question embedding and candidate retrieval (Requirement 1.1, 1.4)."""

from __future__ import annotations

import asyncio

import psycopg

from services.api.domain import Hit, SectionHit
from services.api.store import vector_store


async def embed_question(question: str, embedder) -> list[float]:
    """Embed a question without blocking the event loop.

    sentence-transformers inference is synchronous CPU work taking tens of
    milliseconds. Running it inline would stall every other in-flight request
    on the single container, which is the specific risk flagged for 30-40
    concurrent researchers (Requirement 19), so it goes to a worker thread.
    """
    return await asyncio.to_thread(embedder.encode_one, question)


async def search(
    question: str,
    *,
    conn: psycopg.Connection,
    embedder,
    survey_year: int | None = None,
    limit: int = 8,
) -> list[Hit]:
    """Retrieve the nearest codebook variables for a question."""
    vector = await embed_question(question, embedder)
    return await asyncio.to_thread(
        vector_store.search_variables,
        conn,
        vector,
        survey_year=survey_year,
        limit=limit,
    )


async def search_with_sections(
    question: str,
    *,
    conn: psycopg.Connection,
    embedder,
    survey_year: int | None = None,
    limit: int = 8,
    section_limit: int = 3,
) -> tuple[list[Hit], list[SectionHit]]:
    """Retrieve variables plus the sections they cluster in.

    Section hits give an answer the vocabulary to say *where* in the survey a
    concept lives, which is often what a researcher actually wants.
    """
    vector = await embed_question(question, embedder)

    hits = await asyncio.to_thread(
        vector_store.search_variables,
        conn,
        vector,
        survey_year=survey_year,
        limit=limit,
    )
    sections = await asyncio.to_thread(
        vector_store.search_sections,
        conn,
        vector,
        survey_year=survey_year,
        limit=section_limit,
    )
    return hits, sections


def dedupe_by_variable(hits: list[Hit]) -> list[Hit]:
    """Keep the highest-scoring hit per variable name.

    Relevant once more than one survey year is ingested: the same variable
    appears in each year and would otherwise fill the candidate list with
    near-duplicates of itself.
    """
    best: dict[str, Hit] = {}
    for hit in hits:
        existing = best.get(hit.variable_name)
        if existing is None or hit.similarity > existing.similarity:
            best[hit.variable_name] = hit
    return sorted(best.values(), key=lambda h: h.similarity, reverse=True)
