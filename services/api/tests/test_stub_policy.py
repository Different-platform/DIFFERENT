"""Stub LLM policy tests.

The stub is what runs with no GROQ_API_KEY, which is the default state of
this environment. These tests exist because the stub has already produced a
real, user-visible defect twice: a loose upper-case token scan picked up the
word "NOT" from "NHIS does NOT measure ..." as if it were a variable name,
and the generate stage then asserted an equivalence ("NHIS measures this
with NOT") regardless of what verdict it had been given.
"""

from __future__ import annotations

from services.api.llm.gateway import FakeLLMGateway, Message
from services.api.llm.stub_policy import _candidate_variables, _presence_verdict, stub_handler
from services.api.rag import generate, judge


def _hit(name: str, description: str = "desc") -> object:
    from services.api.domain import Hit

    return Hit(
        variable_name=name,
        survey_year=2023,
        page=1,
        similarity=0.5,
        section_name="Section",
        description=description,
    )


def test_candidate_variables_ignores_prose_words_in_a_negated_sentence():
    """Regression: 'NHIS does NOT measure ...' must not yield 'NOT' as a variable."""
    hits = [_hit("SOCSCLPAR_A")]
    prompt = generate.build_prompt("Does NHIS measure loneliness?", hits, "RELATED")
    variables = _candidate_variables([Message(role="user", content=prompt)])

    assert variables == ["SOCSCLPAR_A"]
    assert "NOT" not in variables


def test_candidate_variables_parses_the_judge_numbered_format():
    hits = [_hit("SOCSCLPAR_A"), _hit("MARSTAT_A")]
    prompt = judge.build_prompt("Does NHIS measure loneliness?", hits)
    variables = _candidate_variables([Message(role="user", content=prompt)])

    assert variables == ["SOCSCLPAR_A", "MARSTAT_A"]


def test_presence_verdict_is_read_from_the_generate_prompt():
    hits = [_hit("SOCSCLPAR_A")]
    for verdict in ("MEASURED", "RELATED", "ABSENT"):
        prompt = generate.build_prompt("question", hits, verdict)
        assert _presence_verdict([Message(role="user", content=prompt)]) == verdict


def test_related_wording_never_asserts_measurement():
    """Regression: must not say 'NHIS measures this with X' under RELATED."""
    hits = [_hit("SOCSCLPAR_A")]
    prompt = generate.build_prompt("Does NHIS measure loneliness?", hits, "RELATED")
    answer = stub_handler("", [Message(role="user", content=prompt)], "generate")

    assert "measures this with" not in answer
    assert "does not measure this directly" in answer
    assert "SOCSCLPAR_A" in answer


def test_absent_wording_never_cites_a_variable_as_the_answer():
    hits = [_hit("HHRESPSA_FLG")]
    prompt = generate.build_prompt("Firearm ownership?", hits, "ABSENT")
    answer = stub_handler("", [Message(role="user", content=prompt)], "generate")

    assert "Not measured in NHIS" in answer


def test_measured_wording_asserts_measurement():
    hits = [_hit("FDSCAT4_A")]
    prompt = generate.build_prompt("How is food insecurity measured?", hits, "MEASURED")
    answer = stub_handler("", [Message(role="user", content=prompt)], "generate")

    assert "NHIS measures this with FDSCAT4_A" in answer


async def test_stub_gateway_end_to_end_through_fake_handler():
    gateway = FakeLLMGateway(handler=stub_handler)
    hits = [_hit("SOCSCLPAR_A")]
    prompt = generate.build_prompt("Does NHIS measure loneliness?", hits, "RELATED")

    response = await gateway.complete(
        system="", messages=[Message(role="user", content=prompt)], stage="generate"
    )

    assert "measures this with" not in response.text
