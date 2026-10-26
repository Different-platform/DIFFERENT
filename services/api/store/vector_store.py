"""pgvector search over the codebook (Requirement 1.1, 1.4).

Retrieval is a SQL query of about fifteen lines, which is exactly why this
project does not wrap it in a framework — when a retrieval result looks wrong,
the query is right here to read.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from services.api.domain import Hit, SectionHit, ValueLabel


def _to_pgvector(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(c)) for c in vector) + "]"


# Structural / design variables that are never a research concept: survey
# weights, design variables, and record identifiers. They must not surface as
# retrieval candidates — a researcher never asks "does NHIS measure WTFA_A?",
# and when a question contains "weighted" or "average" the weight variable
# otherwise ranks top, flipping the presence judge to RELATED and making the
# weight itself the headline "measure". They remain usable in generated SQL
# (the generator is told about WTFA_A explicitly); this only removes them from
# the retrieval/judge candidate set.
NON_SUBSTANTIVE_VARIABLES: tuple[str, ...] = (
    "WTFA_A",
    "WTIA_A",
    "WTFA_SA",
    "WTIA_SA",
    "PSTRAT",
    "PPSU",
    "HHX",
    "RECTYPE",
    "SRVY_YR",
)


# 1 - cosine_distance gives cosine similarity, which is what the UI shows.
_SEARCH_VARIABLES = """
SELECT
    id,
    variable_name,
    survey_year,
    page,
    section_code,
    section_name,
    module,
    description,
    question_text,
    keywords,
    universe,
    universe_description,
    data_type,
    is_recode,
    1 - (embedding <=> %(query)s::vector) AS similarity
FROM codebook_variables
WHERE embedding IS NOT NULL
  AND (%(survey_year)s::int IS NULL OR survey_year = %(survey_year)s::int)
  AND NOT (variable_name = ANY(%(exclude)s))
ORDER BY embedding <=> %(query)s::vector
LIMIT %(limit)s
"""

_SEARCH_SECTIONS = """
SELECT
    code,
    name,
    survey_year,
    n_variables,
    1 - (embedding <=> %(query)s::vector) AS similarity
FROM sections
WHERE embedding IS NOT NULL
  AND (%(survey_year)s::int IS NULL OR survey_year = %(survey_year)s::int)
ORDER BY embedding <=> %(query)s::vector
LIMIT %(limit)s
"""

_LOAD_LABELS = """
SELECT variable_id, code, label, is_sentinel, frequency, percent
FROM value_labels
WHERE variable_id = ANY(%(ids)s)
ORDER BY variable_id, code
"""


def search_variables(
    conn: psycopg.Connection,
    query_vector: list[float],
    *,
    survey_year: int | None = None,
    limit: int = 8,
) -> list[Hit]:
    """Nearest codebook variables, with their value labels attached.

    Value labels come along because every downstream stage needs them: the
    judge reasons about response options, and SQL verification compares
    generated codes against them.
    """
    vector = _to_pgvector(query_vector)
    params = {
        "query": vector,
        "survey_year": survey_year,
        "limit": limit,
        "exclude": list(NON_SUBSTANTIVE_VARIABLES),
    }

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SEARCH_VARIABLES, params)
        rows = cur.fetchall()

        if not rows:
            return []

        ids = [row["id"] for row in rows]
        cur.execute(_LOAD_LABELS, {"ids": ids})
        labels: dict[int, list[ValueLabel]] = {}
        for label_row in cur.fetchall():
            labels.setdefault(label_row["variable_id"], []).append(
                ValueLabel(
                    code=label_row["code"],
                    label=label_row["label"],
                    is_sentinel=label_row["is_sentinel"],
                    frequency=label_row["frequency"],
                    percent=label_row["percent"],
                )
            )

    return [
        Hit(
            variable_name=row["variable_name"],
            survey_year=row["survey_year"],
            page=row["page"],
            similarity=float(row["similarity"]),
            section_code=row["section_code"],
            section_name=row["section_name"],
            module=row["module"],
            description=row["description"],
            question_text=row["question_text"],
            keywords=row["keywords"],
            universe=row["universe"],
            universe_description=row["universe_description"],
            data_type=row["data_type"],
            is_recode=row["is_recode"],
            value_labels=labels.get(row["id"], []),
        )
        for row in rows
    ]


def search_sections(
    conn: psycopg.Connection,
    query_vector: list[float],
    *,
    survey_year: int | None = None,
    limit: int = 3,
) -> list[SectionHit]:
    vector = _to_pgvector(query_vector)
    params = {"query": vector, "survey_year": survey_year, "limit": limit}

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SEARCH_SECTIONS, params)
        return [
            SectionHit(
                code=row["code"],
                name=row["name"],
                survey_year=row["survey_year"],
                similarity=float(row["similarity"]),
                n_variables=row["n_variables"],
            )
            for row in cur.fetchall()
        ]


def get_variable(
    conn: psycopg.Connection, survey_year: int, variable_name: str
) -> Hit | None:
    """Fetch one variable by name, for the detail endpoint."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, variable_name, survey_year, page, section_code,
                   section_name, module, description, question_text, keywords,
                   universe, universe_description, data_type, is_recode
            FROM codebook_variables
            WHERE survey_year = %s AND variable_name = %s
            """,
            (survey_year, variable_name),
        )
        row = cur.fetchone()
        if row is None:
            return None

        cur.execute(_LOAD_LABELS, {"ids": [row["id"]]})
        value_labels = [
            ValueLabel(
                code=r["code"],
                label=r["label"],
                is_sentinel=r["is_sentinel"],
                frequency=r["frequency"],
                percent=r["percent"],
            )
            for r in cur.fetchall()
        ]

    return Hit(
        variable_name=row["variable_name"],
        survey_year=row["survey_year"],
        page=row["page"],
        similarity=1.0,
        section_code=row["section_code"],
        section_name=row["section_name"],
        module=row["module"],
        description=row["description"],
        question_text=row["question_text"],
        keywords=row["keywords"],
        universe=row["universe"],
        universe_description=row["universe_description"],
        data_type=row["data_type"],
        is_recode=row["is_recode"],
        value_labels=value_labels,
    )


def search_variables_by_text(
    conn: psycopg.Connection,
    query: str,
    *,
    survey_year: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Lexical search for the variable browser, using the generated tsvector.

    Separate from vector search on purpose: when a researcher types an exact
    variable name they want that variable, not its nearest neighbours.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT variable_name, survey_year, page, section_name, description
            FROM codebook_variables
            WHERE (%(survey_year)s::int IS NULL
                   OR survey_year = %(survey_year)s::int)
              AND (variable_name ILIKE %(like)s
                   OR tsv @@ plainto_tsquery('english', %(query)s))
            ORDER BY
                (variable_name ILIKE %(like)s) DESC,
                variable_name
            LIMIT %(limit)s
            """,
            {
                "survey_year": survey_year,
                "query": query,
                "like": f"%{query}%",
                "limit": limit,
            },
        )
        return [dict(row) for row in cur.fetchall()]


def survey_table_exists(conn: psycopg.Connection, survey_year: int) -> bool:
    """Whether the microdata table for a year has been ingested.

    The verified-statistics path can only run against a loaded year; the
    orchestrator checks this before attempting SQL generation so an un-loaded
    year yields an honest limitation rather than a failed query.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.survey_{survey_year}",))
        return cur.fetchone()[0] is not None


def loaded_survey_years(conn: psycopg.Connection) -> list[int]:
    """Which years have a `survey_YYYY` microdata table loaded.

    Distinct from `available_years`, which reports *codebook* coverage. A year
    can have its codebook indexed (so Q&A works) without its microdata loaded
    (so verified statistics do not). Surfacing this on /api/health lets an
    operator see at a glance which years can answer a `data` question.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT regexp_replace(table_name, '^survey_', '')::int AS year
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name ~ '^survey_[0-9]+$'
            ORDER BY year
            """
        )
        return [row[0] for row in cur.fetchall()]


def available_years(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT survey_year,
                   count(*) AS n_variables,
                   count(*) FILTER (WHERE embedding IS NOT NULL) AS n_embedded
            FROM codebook_variables
            GROUP BY survey_year
            ORDER BY survey_year
            """
        )
        return [dict(row) for row in cur.fetchall()]


def decimal_to_number(value: Decimal) -> float | int:
    """Render a numeric code for JSON without trailing-zero noise."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)
