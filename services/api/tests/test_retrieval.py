"""Retrieval dedup for a multi-year comparison question (rag/retrieval.py)."""

from __future__ import annotations

from services.api.domain import Hit
from services.api.rag.retrieval import dedupe_by_variable_and_year


def _hit(name: str, year: int, similarity: float) -> Hit:
    return Hit(variable_name=name, survey_year=year, page=1, similarity=similarity)


def test_the_same_variable_in_two_different_years_is_kept_as_two_hits():
    """K6SPD_A only exists in 2021 within the loaded corpus; a dedupe that
    collapses across years the way dedupe_by_variable does would lose that
    fact entirely for a question comparing 2021 to 2023."""
    hits = [_hit("K6SPD_A", 2021, 0.6), _hit("AGEP_A", 2023, 0.5)]
    result = dedupe_by_variable_and_year(hits)
    assert {(h.variable_name, h.survey_year) for h in result} == {
        ("K6SPD_A", 2021), ("AGEP_A", 2023),
    }


def test_the_same_variable_and_year_pair_keeps_only_the_higher_similarity():
    hits = [_hit("AGEP_A", 2023, 0.3), _hit("AGEP_A", 2023, 0.7)]
    result = dedupe_by_variable_and_year(hits)
    assert len(result) == 1
    assert result[0].similarity == 0.7


def test_results_are_sorted_by_similarity_descending():
    hits = [_hit("A", 2021, 0.2), _hit("B", 2022, 0.9), _hit("C", 2023, 0.5)]
    result = dedupe_by_variable_and_year(hits)
    assert [h.variable_name for h in result] == ["B", "C", "A"]


def test_empty_input_returns_empty_output():
    assert dedupe_by_variable_and_year([]) == []
