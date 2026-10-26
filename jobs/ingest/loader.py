"""Load parsed codebook records into Postgres.

One survey year is loaded in a single transaction. Either the whole year
lands or none of it does — the same principle the abort guard enforces
upstream, applied to the write path so a mid-load failure cannot leave a
half-indexed year behind.

Re-ingesting a year replaces it, so the job is safe to re-run after a parser
fix.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from jobs.ingest.embedder import Embedder, to_pgvector
from jobs.ingest.records import ParsedVariable, build_embed_text


@dataclass
class LoadSummary:
    survey_year: int
    sections: int
    variables: int
    value_labels: int

    def __str__(self) -> str:
        return (
            f"loaded {self.survey_year}: {self.variables} variables, "
            f"{self.value_labels} value labels, {self.sections} sections"
        )


def _derive_sections(variables: list[ParsedVariable]) -> dict[str, tuple[str, int]]:
    """Map section_code -> (section_name, variable_count)."""
    sections: dict[str, tuple[str, int]] = {}
    for variable in variables:
        if not variable.section_code:
            continue
        name, count = sections.get(
            variable.section_code, (variable.section_name or variable.section_code, 0)
        )
        sections[variable.section_code] = (name, count + 1)
    return sections


def load_year(
    conn: psycopg.Connection,
    survey_year: int,
    variables: list[ParsedVariable],
    embedder: Embedder,
) -> LoadSummary:
    """Replace all stored data for `survey_year` with `variables`."""
    if not variables:
        raise ValueError("refusing to load zero variables; check the abort guard")

    # Embed in bulk: one model call for variables, one for sections.
    embed_texts = [build_embed_text(variable) for variable in variables]
    variable_vectors = embedder.encode(embed_texts)

    sections = _derive_sections(variables)
    section_codes = sorted(sections)
    section_vectors = (
        embedder.encode([f"{sections[code][0]} ({code})" for code in section_codes])
        if section_codes
        else []
    )

    with conn.cursor() as cur:
        # Idempotent re-ingest. value_labels cascade from codebook_variables.
        cur.execute(
            "DELETE FROM codebook_variables WHERE survey_year = %s", (survey_year,)
        )
        cur.execute("DELETE FROM sections WHERE survey_year = %s", (survey_year,))

        for code, vector in zip(section_codes, section_vectors, strict=True):
            name, count = sections[code]
            cur.execute(
                """
                INSERT INTO sections (survey_year, code, name, n_variables, embedding)
                VALUES (%s, %s, %s, %s, %s::vector)
                """,
                (survey_year, code, name, count, to_pgvector(vector)),
            )

        label_count = 0
        for variable, embed_text, vector in zip(
            variables, embed_texts, variable_vectors, strict=True
        ):
            cur.execute(
                """
                INSERT INTO codebook_variables (
                    survey_year, variable_name, section_code, section_name,
                    module, description, question_text, keywords, data_type,
                    universe, universe_description, page, is_recode,
                    source_era, embed_text, embedding
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s::vector
                )
                RETURNING id
                """,
                (
                    variable.survey_year,
                    variable.variable_name,
                    variable.section_code,
                    variable.section_name,
                    variable.module,
                    variable.description,
                    variable.question_text,
                    variable.keywords,
                    variable.data_type,
                    variable.universe,
                    variable.universe_description,
                    variable.page,
                    variable.is_recode,
                    variable.source_era,
                    embed_text,
                    to_pgvector(vector),
                ),
            )
            variable_id = cur.fetchone()[0]

            if variable.value_labels:
                cur.executemany(
                    """
                    INSERT INTO value_labels
                        (variable_id, code, label, frequency, percent, is_sentinel)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            variable_id,
                            vl.code,
                            vl.label,
                            vl.frequency,
                            vl.percent,
                            vl.is_sentinel,
                        )
                        for vl in variable.value_labels
                    ],
                )
                label_count += len(variable.value_labels)

        _refresh_variable_availability(cur)

    return LoadSummary(
        survey_year=survey_year,
        sections=len(section_codes),
        variables=len(variables),
        value_labels=label_count,
    )


def _refresh_variable_availability(cur: psycopg.Cursor) -> None:
    """Recompute which years each variable appears in.

    Derived from stored records, never from search results — an answer
    implying a variable was dropped when it was merely absent from a result
    set would be misleading.
    """
    cur.execute("DELETE FROM variable_availability")
    cur.execute(
        """
        INSERT INTO variable_availability (variable_name, years)
        SELECT variable_name, array_agg(DISTINCT survey_year ORDER BY survey_year)
        FROM codebook_variables
        GROUP BY variable_name
        """
    )
