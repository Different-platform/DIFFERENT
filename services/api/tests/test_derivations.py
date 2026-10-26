"""Known derived measures (rag/derivations.py)."""

from __future__ import annotations

import pytest

from services.api.rag.derivations import required_inputs


@pytest.mark.parametrize(
    "question",
    [
        "What is the weighted mean BMI for adults?",
        "average bmi in 2021",
        "mean body mass index of adults",
        "What is the average Body-Mass Index?",
    ],
)
def test_bmi_questions_require_height_and_weight(question):
    assert required_inputs(question) == ["HEIGHTTC_A", "WEIGHTLBTC_A"]


@pytest.mark.parametrize(
    "question",
    [
        "What is the average age of adults?",
        "How is food insecurity measured?",
        "distribution of BMICAT_A",  # a real variable, not a derivation
        "",
    ],
)
def test_unrelated_questions_require_nothing(question):
    assert required_inputs(question) == []
