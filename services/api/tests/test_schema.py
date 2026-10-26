"""Schema assertions (Task 2.7, Requirement 20.1, Requirement 5.1/5.2).

These guard properties that are easy to break with a careless migration and
expensive to discover later — a non-nullable user_id would block the no-auth
requirement, and an integer code column would silently truncate fractional
sentinels like BMI_A's 99.9.
"""

from __future__ import annotations

import pytest

from services.api.tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def test_conversations_user_id_is_nullable_uuid(db_conn):
    """Req 20.1: reserved for future auth, but no login in this version."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'conversations' AND column_name = 'user_id'
            """
        )
        row = cur.fetchone()

    assert row is not None, "conversations.user_id is missing"
    data_type, is_nullable = row
    assert data_type == "uuid"
    assert is_nullable == "YES"


def test_value_label_code_accepts_fractional_sentinel(db_conn):
    """A numeric code column must hold BMI_A's 99.9 without truncation.

    design.md specifies `int` here; that would store 99.9 as 99 and the SQL
    verifier would then compare against the wrong sentinel.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO codebook_variables
                (survey_year, variable_name, description, page, source_era)
            VALUES (2023, '_TEST_BMI', 'test', 1, '2019plus')
            RETURNING id
            """
        )
        variable_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO value_labels (variable_id, code, label, is_sentinel)
            VALUES (%s, 99.9, 'Unknown', true)
            RETURNING code
            """,
            (variable_id,),
        )
        stored = cur.fetchone()[0]

    assert float(stored) == pytest.approx(99.9), "fractional sentinel was truncated"


def test_same_code_holds_opposite_sentinel_verdicts(db_conn):
    """Req 5.1: sentinel status is per variable, never a global rule.

    Code 7 is a real racial category on HISPALLP_A and a sentinel elsewhere.
    The schema must permit both simultaneously.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO codebook_variables
                (survey_year, variable_name, description, page, source_era)
            VALUES (2023, '_TEST_HISP', 'race', 31, '2019plus'),
                   (2023, '_TEST_OTHER', 'other', 50, '2019plus')
            RETURNING id, variable_name
            """
        )
        ids = {name: vid for vid, name in cur.fetchall()}

        cur.execute(
            """
            INSERT INTO value_labels (variable_id, code, label, is_sentinel)
            VALUES (%s, 7, 'Other single and multiple races', false),
                   (%s, 7, 'Refused', true)
            """,
            (ids["_TEST_HISP"], ids["_TEST_OTHER"]),
        )

        cur.execute(
            """
            SELECT cv.variable_name, vl.is_sentinel
            FROM value_labels vl
            JOIN codebook_variables cv ON cv.id = vl.variable_id
            WHERE vl.code = 7 AND cv.variable_name LIKE '_TEST_%%'
            """
        )
        verdicts = dict(cur.fetchall())

    assert verdicts["_TEST_HISP"] is False
    assert verdicts["_TEST_OTHER"] is True


def test_value_labels_cascade_on_variable_delete(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO codebook_variables
                (survey_year, variable_name, description, page, source_era)
            VALUES (2023, '_TEST_CASCADE', 'test', 1, '2019plus')
            RETURNING id
            """
        )
        variable_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO value_labels (variable_id, code, label, is_sentinel)"
            " VALUES (%s, 1, 'Yes', false)",
            (variable_id,),
        )
        cur.execute(
            "DELETE FROM codebook_variables WHERE id = %s", (variable_id,)
        )
        cur.execute(
            "SELECT count(*) FROM value_labels WHERE variable_id = %s",
            (variable_id,),
        )
        assert cur.fetchone()[0] == 0


def test_generated_tsv_column_is_populated(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO codebook_variables
                (survey_year, variable_name, description, question_text,
                 keywords, page, source_era)
            VALUES (2023, '_TEST_TSV', 'Body mass index', 'Derived value',
                    'bmi obesity', 88, '2019plus')
            RETURNING tsv::text
            """
        )
        tsv = cur.fetchone()[0]

    assert tsv, "generated tsv column did not populate"
    assert "bmi" in tsv or "bodi" in tsv


def test_source_era_rejects_unknown_value(db_conn):
    import psycopg

    with db_conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO codebook_variables
                    (survey_year, variable_name, description, page, source_era)
                VALUES (2023, '_TEST_ERA', 'test', 1, 'not_a_real_era')
                """
            )


def test_retrieval_excludes_design_variables(db_conn):
    """Weights, design vars, and record ids must never be retrieval candidates.

    A researcher never asks "does NHIS measure WTFA_A?"; surfacing the weight
    as a candidate flips the presence judge and makes the weight the headline
    "measure". The exclusion is enforced in the search SQL, so a plain query
    (here with a zero vector, no embedder needed) must return none of them.
    """
    from services.api.store import vector_store

    hits = vector_store.search_variables(
        db_conn, [0.0] * 768, survey_year=2023, limit=50
    )
    names = {h.variable_name for h in hits}
    leaked = names & set(vector_store.NON_SUBSTANTIVE_VARIABLES)
    assert leaked == set(), f"design variables leaked into retrieval: {leaked}"
