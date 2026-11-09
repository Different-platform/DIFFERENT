"""GET /api/years — which survey years are actually queryable.

Backed by stored records rather than a hardcoded list, so the UI's year
selector reflects what has really been ingested. Offering 2017-2023 in a
dropdown when only 2023 is loaded would produce confidently empty answers.
"""

from __future__ import annotations

from fastapi import APIRouter

from services.api.domain import SURVEY_YEARS
from services.api.store import db, vector_store

router = APIRouter(tags=["years"])


@router.get("/api/years")
async def list_years() -> dict:
    ingested = await db.run_db(vector_store.available_years)
    by_year = {row["survey_year"]: row for row in ingested}

    return {
        "years": [
            {
                "year": year,
                "has_codebook": year in by_year,
                "n_variables": by_year.get(year, {}).get("n_variables", 0),
            }
            for year in SURVEY_YEARS
        ],
        "ingested": sorted(by_year),
    }
