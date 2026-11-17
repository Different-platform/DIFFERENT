"""The presence judge's own selection of which candidate answers the question.

`closest` used to be hardcoded to `hits[0]` — the top retrieval-similarity
candidate — regardless of what the model actually decided. That is exactly
why VIRAPP12M_A (the real telehealth-use variable, retrieval rank 5) lost its
citation to three higher-ranked but less relevant variables: nothing had
ever asked the judge which candidate was actually right. These tests cover
the fix: the judge names its own best match(es), validated against the
candidates it was actually shown.
"""

from __future__ import annotations

import pytest

from services.api.domain import Hit
from services.api.llm.gateway import FakeLLMGateway, LLMGatewayError
from services.api.rag import judge


def _hit(name: str, similarity: float = 0.5) -> Hit:
    return Hit(
        variable_name=name, survey_year=2023, page=1, similarity=similarity,
        section_name="Section", description=f"description of {name}",
    )


# --- parsing -------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected_verdict", "expected_best"),
    [
        ('{"verdict": "MEASURED", "why": "x", "best": ["A_VAR"]}', "MEASURED", ["A_VAR"]),
        ('{"verdict": "RELATED", "why": "x", "best": ["B_VAR"]}', "RELATED", ["B_VAR"]),
        ('{"verdict": "ABSENT", "why": "x", "best": []}', "ABSENT", []),
        ('{"verdict": "MEASURED", "why": "x"}', "MEASURED", []),  # no best at all
        ('{"verdict": "MEASURED", "why": "x", "best": "A_VAR"}', "MEASURED", []),  # not a list
        ("not json", None, []),
    ],
)
def test_parse_verdict(raw, expected_verdict, expected_best):
    verdict, _why, best = judge._parse_verdict(raw)
    assert verdict == expected_verdict
    assert best == expected_best


# --- classify_presence: grounding the judge's own selection ---------------
async def test_the_judges_named_best_match_is_cited_even_when_ranked_lower():
    """The exact bug: VIRAPP12M_A ranked 5th by retrieval similarity, behind
    three internet-health variables, but is the actually-correct answer."""
    hits = [
        _hit("HITCOMM_A", 0.46),
        _hit("HITLOOK_A", 0.45),
        _hit("HITTEST_A", 0.45),
        _hit("ACCSSHOM_A", 0.41),
        _hit("VIRAPP12M_A", 0.41),
    ]
    gateway = FakeLLMGateway(
        responses=[
            '{"verdict": "MEASURED", "why": "VIRAPP12M_A directly asks about '
            'virtual appointments.", "best": ["VIRAPP12M_A"]}'
        ]
    )
    verdict, closest, _why, best_hits = await judge.classify_presence(
        "Does NHIS measure telehealth use?", hits, gateway=gateway
    )
    assert verdict == "MEASURED"
    assert closest.variable_name == "VIRAPP12M_A"
    assert [h.variable_name for h in best_hits] == ["VIRAPP12M_A"]


async def test_a_name_not_among_the_candidates_is_dropped_not_trusted():
    """Grounding: the judge cannot cite a variable it was never shown."""
    hits = [_hit("REAL_VAR_A", 0.5)]
    gateway = FakeLLMGateway(
        responses=[
            '{"verdict": "MEASURED", "why": "x", "best": ["MADE_UP_VAR_A"]}'
        ]
    )
    verdict, closest, _why, best_hits = await judge.classify_presence(
        "question", hits, gateway=gateway
    )
    assert verdict == "MEASURED"
    # Invented name dropped; degrades to the old top-rank behavior rather
    # than citing something that was never actually retrieved.
    assert closest.variable_name == "REAL_VAR_A"
    assert best_hits == [hits[0]]


async def test_best_can_list_more_than_one_variable_for_measured():
    hits = [_hit("HEIGHTTC_A", 0.5), _hit("WEIGHTLBTC_A", 0.4)]
    gateway = FakeLLMGateway(
        responses=[
            '{"verdict": "MEASURED", "why": "BMI is derived from both.", '
            '"best": ["HEIGHTTC_A", "WEIGHTLBTC_A"]}'
        ]
    )
    _verdict, _closest, _why, best_hits = await judge.classify_presence(
        "question", hits, gateway=gateway
    )
    assert [h.variable_name for h in best_hits] == ["HEIGHTTC_A", "WEIGHTLBTC_A"]


async def test_related_still_carries_exactly_the_models_chosen_closest():
    hits = [_hit("SOCSCLPAR_A", 0.5), _hit("MARSTAT_A", 0.3)]
    gateway = FakeLLMGateway(
        responses=[
            '{"verdict": "RELATED", "why": "adjacent but distinct.", '
            '"best": ["SOCSCLPAR_A"]}'
        ]
    )
    verdict, closest, _why, best_hits = await judge.classify_presence(
        "Does NHIS measure loneliness?", hits, gateway=gateway
    )
    assert verdict == "RELATED"
    assert closest.variable_name == "SOCSCLPAR_A"
    assert len(best_hits) == 1


async def test_absent_carries_no_best_hits():
    hits = [_hit("UNRELATED_A", 0.4)]
    gateway = FakeLLMGateway(
        responses=['{"verdict": "ABSENT", "why": "not measured.", "best": []}']
    )
    verdict, closest, _why, best_hits = await judge.classify_presence(
        "question", hits, gateway=gateway
    )
    assert verdict == "ABSENT"
    assert closest is None
    assert best_hits == []


async def test_no_hits_at_all_is_absent_without_a_gateway_call():
    gateway = FakeLLMGateway(responses=["unused"])
    verdict, closest, _why, best_hits = await judge.classify_presence(
        "question", [], gateway=gateway
    )
    assert verdict == "ABSENT"
    assert closest is None
    assert best_hits == []
    assert len(gateway.calls) == 0


async def test_an_unparseable_response_falls_back_to_related_with_top_rank():
    hits = [_hit("TOP_RANK_A", 0.5)]

    class _Garbled:
        async def complete(self, **kwargs):
            from services.api.llm.gateway import LLMResponse

            return LLMResponse(text="not valid json at all", model="fake")

    verdict, closest, _why, best_hits = await judge.classify_presence(
        "question", hits, gateway=_Garbled()
    )
    assert verdict == "RELATED"
    assert closest.variable_name == "TOP_RANK_A"
    assert best_hits == [hits[0]]


async def test_a_gateway_error_propagates():
    class _Broken:
        async def complete(self, **kwargs):
            raise LLMGatewayError("down")

    with pytest.raises(LLMGatewayError):
        await judge.classify_presence("question", [_hit("A_VAR")], gateway=_Broken())
