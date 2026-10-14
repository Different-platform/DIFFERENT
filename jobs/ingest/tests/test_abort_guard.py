"""Ingestion abort guard tests (Requirements 9.1, 9.2, 9.3).

SAFETY-CRITICAL. Includes design.md Property 9.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from jobs.ingest.abort_guard import check_abort


def test_aborts_below_threshold():
    decision = check_abort(survey_year=2021, parsed_count=0, threshold=100)
    assert decision.should_abort is True
    assert "2021" in decision.report
    assert "0" in decision.report


def test_proceeds_at_threshold():
    assert check_abort(2023, 100, 100).should_abort is False


def test_proceeds_above_threshold():
    decision = check_abort(survey_year=2023, parsed_count=647, threshold=100)
    assert decision.should_abort is False
    assert "647" in decision.report


def test_zero_parse_is_the_motivating_case():
    """Three of five years once silently parsed to zero and loaded anyway."""
    decision = check_abort(survey_year=2020, parsed_count=0, threshold=100)
    assert decision.should_abort is True
    assert "No rows written" in decision.report


def test_report_always_names_year_and_count():
    """Req 9.3: the abort report must identify what failed and by how much."""
    for count, threshold in ((0, 100), (99, 100), (647, 100)):
        report = check_abort(2023, count, threshold).report
        assert "2023" in report
        assert str(count) in report


def test_negative_count_is_a_caller_bug_not_an_abort():
    with pytest.raises(ValueError):
        check_abort(survey_year=2023, parsed_count=-1, threshold=100)


@given(
    survey_year=st.integers(min_value=2017, max_value=2023),
    parsed_count=st.integers(min_value=0, max_value=5000),
    threshold=st.integers(min_value=0, max_value=1000),
)
def test_property_9_ingestion_abort_guard(
    survey_year: int, parsed_count: int, threshold: int
):
    """Feature: differ-platform, Property 9: Ingestion abort guard.

    For all combinations of survey year and parsed-variable count, if the
    count is below the configured threshold the job must abort and produce a
    report naming the year and the count; otherwise it must proceed.
    """
    decision = check_abort(survey_year, parsed_count, threshold)

    assert decision.should_abort == (parsed_count < threshold)
    assert str(survey_year) in decision.report
    assert str(parsed_count) in decision.report
    assert decision.survey_year == survey_year
    assert decision.parsed_count == parsed_count
    assert decision.threshold == threshold
