"""SQL verifier tests (Requirements 6.1, 6.2, 6.3).

SAFETY-CRITICAL. Task 10.3 (Property 6) and Task 10.4 (hand-written fragment
tests). The verifier is pure, so none of this needs a database.

The fixture codebook mirrors the real HISPALLP_A entry — code 7 is the real
"Other single and multiple races" category, 97/98/99 are the sentinels. The
exact mistake the verifier exists to catch is SQL that writes `77`/`88`/`99`
or `7,8,9`-as-sentinels against this variable.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from services.api.rag.verify import Problem, check

CODEBOOK = {
    "hispallp_a": frozenset(Decimal(c) for c in (1, 2, 3, 4, 5, 6, 7, 97, 98, 99)),
    "smkev_a": frozenset(Decimal(c) for c in (1, 2, 7, 8, 9)),
    "bmicat_a": frozenset(Decimal(str(c)) for c in ("1", "2", "3", "4", "9.9")),
}


def kinds(problems: list[Problem]) -> set[str]:
    return {p.kind for p in problems}


# ---------------------------------------------------------------------------
# Task 10.4 — hand-written fragments: IN / NOT IN / = / BETWEEN
# ---------------------------------------------------------------------------
def test_equality_with_defined_code_passes():
    sql = "SELECT count(*) FROM survey_2023 WHERE HISPALLP_A = 7"
    assert check(sql, CODEBOOK) == []


def test_equality_with_undefined_code_fails():
    sql = "SELECT count(*) FROM survey_2023 WHERE HISPALLP_A = 77"
    problems = check(sql, CODEBOOK)
    assert kinds(problems) == {"unknown_code"}
    assert problems[0].column == "HISPALLP_A"
    assert problems[0].code == "77"


def test_not_in_with_invented_sentinels_fails():
    """The canonical failure: real sentinels are 97/98/99, model wrote 77/88/99."""
    sql = "SELECT avg(x) FROM survey_2023 WHERE HISPALLP_A NOT IN (77, 88, 99)"
    problems = check(sql, CODEBOOK)
    bad = {p.code for p in problems if p.kind == "unknown_code"}
    assert bad == {"77", "88"}  # 99 is a real sentinel, so it is fine


def test_in_list_all_defined_passes():
    sql = "SELECT count(*) FROM survey_2023 WHERE HISPALLP_A IN (1, 2, 3, 7)"
    assert check(sql, CODEBOOK) == []


def test_in_list_one_undefined_fails():
    sql = "SELECT count(*) FROM survey_2023 WHERE HISPALLP_A IN (1, 2, 42)"
    problems = check(sql, CODEBOOK)
    assert kinds(problems) == {"unknown_code"}
    assert {p.code for p in problems} == {"42"}


def test_between_with_defined_endpoints_passes():
    sql = "SELECT count(*) FROM survey_2023 WHERE HISPALLP_A BETWEEN 1 AND 7"
    assert check(sql, CODEBOOK) == []


def test_between_with_undefined_endpoint_fails():
    sql = "SELECT count(*) FROM survey_2023 WHERE HISPALLP_A BETWEEN 1 AND 50"
    problems = check(sql, CODEBOOK)
    assert {p.code for p in problems} == {"50"}


def test_reversed_comparison_is_checked():
    sql = "SELECT count(*) FROM survey_2023 WHERE 77 = HISPALLP_A"
    assert kinds(check(sql, CODEBOOK)) == {"unknown_code"}


def test_fractional_sentinel_code_is_defined():
    sql = "SELECT avg(BMICAT_A) FROM survey_2023 WHERE BMICAT_A != 9.9"
    assert check(sql, CODEBOOK) == []


# ---------------------------------------------------------------------------
# Statement-shape guards
# ---------------------------------------------------------------------------
def test_write_statement_is_rejected():
    assert "forbidden_statement" in kinds(check("DELETE FROM survey_2023", CODEBOOK))


def test_drop_is_rejected():
    assert "forbidden_statement" in kinds(
        check("SELECT 1; DROP TABLE survey_2023", CODEBOOK)
    )


def test_multiple_statements_rejected():
    problems = check("SELECT 1 FROM survey_2023; SELECT 2 FROM survey_2023", CODEBOOK)
    assert "multiple_statements" in kinds(problems)


def test_non_select_rejected():
    assert "not_a_select" in kinds(check("EXPLAIN SELECT 1", CODEBOOK))


def test_cte_select_allowed():
    sql = (
        "WITH t AS (SELECT HISPALLP_A FROM survey_2023 WHERE HISPALLP_A = 1) "
        "SELECT count(*) FROM t"
    )
    assert check(sql, CODEBOOK) == []


def test_empty_query_is_a_problem():
    assert kinds(check("   ", CODEBOOK)) == {"empty"}


def test_clean_query_has_no_false_positive():
    sql = "SELECT count(*) FROM survey_2023 WHERE HISPALLP_A = 1"
    assert check(sql, CODEBOOK) == []


def test_comment_smuggled_write_is_stripped():
    sql = (
        "SELECT count(*) FROM survey_2023 -- DELETE FROM survey_2023\n"
        "WHERE HISPALLP_A = 1"
    )
    assert check(sql, CODEBOOK) == []


def test_uncoded_design_columns_are_not_checked():
    sql = "SELECT avg(WTFA_A) FROM survey_2023 WHERE survey_year = 2023 AND WTFA_A > 0"
    assert check(sql, CODEBOOK) == []


# ---------------------------------------------------------------------------
# Property 6: SQL verifier correctness
# ---------------------------------------------------------------------------
@given(codes=st.lists(st.integers(min_value=0, max_value=200), min_size=1, max_size=6))
def test_property_6_verifier_accepts_iff_all_codes_defined(codes):
    """Feature: differ-platform, Property 6: SQL verifier correctness.

    For a query referencing a set of codes against a known column, check()
    returns no unknown_code problem iff every referenced code is a defined
    value label for that column.
    """
    code_list = ", ".join(str(c) for c in codes)
    sql = f"SELECT count(*) FROM survey_2023 WHERE HISPALLP_A IN ({code_list})"
    problems = check(sql, CODEBOOK)
    unknown = {p.code for p in problems if p.kind == "unknown_code"}

    expected_unknown = {
        str(c) for c in codes if Decimal(c) not in CODEBOOK["hispallp_a"]
    }
    assert unknown == expected_unknown
    all_defined = all(Decimal(c) in CODEBOOK["hispallp_a"] for c in codes)
    assert (unknown == set()) is all_defined
