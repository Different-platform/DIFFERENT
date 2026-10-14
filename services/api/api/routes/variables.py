"""Variable lookup endpoints (Requirements 1.1, 2.1).

Separate from /api/chat because an exact-name lookup should not cost four
model calls. A researcher who types "HISPALLP_A" wants that record.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services.api.domain import SURVEY_YEARS_MAX, SURVEY_YEARS_MIN
from services.api.store import db, vector_store

router = APIRouter(tags=["variables"])


@router.get("/api/variables/search")
async def search_variables(
    q: str = Query(min_length=1, max_length=200),
    year: int | None = Query(default=None, ge=SURVEY_YEARS_MIN, le=SURVEY_YEARS_MAX),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    results = await db.run_db(
        vector_store.search_variables_by_text, q, survey_year=year, limit=limit
    )
    return {"query": q, "year": year, "count": len(results), "results": results}


@router.get("/api/variables/{year}/{name}")
async def get_variable(year: int, name: str) -> dict:
    hit = await db.run_db(vector_store.get_variable, year, name.upper())
    if hit is None:
        raise HTTPException(
            status_code=404,
            detail=f"{name.upper()} not found for survey year {year}",
        )

    return {
        "variable_name": hit.variable_name,
        "survey_year": hit.survey_year,
        "page": hit.page,
        "section_code": hit.section_code,
        "section_name": hit.section_name,
        "module": hit.module,
        "description": hit.description,
        "question_text": hit.question_text,
        "keywords": hit.keywords,
        "data_type": hit.data_type,
        "universe": hit.universe,
        "universe_description": hit.universe_description,
        "is_recode": hit.is_recode,
        "citation": hit.to_citation().to_dict(),
        "value_labels": [
            {
                "code": vector_store.decimal_to_number(vl.code),
                "label": vl.label,
                "is_sentinel": vl.is_sentinel,
                "frequency": vl.frequency,
                "percent": float(vl.percent) if vl.percent is not None else None,
            }
            for vl in hit.value_labels
        ],
    }
