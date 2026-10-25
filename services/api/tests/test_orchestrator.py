"""Orchestrator tests (Requirements 3.2, 3.3, 15.1), including Properties 1, 3, 10.

The database and every LLM-backed stage are replaced with recorders, so these
run with no Postgres and no provider account. What is under test is the
pipeline's *shape*: which stages run, in what order, and what the response
carries for each verdict.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from services.api import orchestrator as orch
from services.api.domain import Hit, ValueLabel
from services.api.llm.gateway import FakeLLMGateway
from services.api.rag import generate, judge, retrieval, router, rewrite, sql_gen
from services.api.store import readonly_store, relational_store, vector_store

CONVERSATION_UUID = UUID("11111111-1111-1111-1111-111111111111")


def make_hit(name: str, similarity: float = 0.6, page: int = 10) -> Hit:
    from decimal import Decimal

    return Hit(
        variable_name=name,
        survey_year=2023,
        page=page,
        similarity=similarity,
        section_code="FDS",
        section_name="Food Security",
        description=f"description of {name}",
        question_text=f"question for {name}",
        value_labels=[
            ValueLabel(code=Decimal(1), label="Yes", is_sentinel=False),
            ValueLabel(code=Decimal(2), label="No", is_sentinel=False),
            ValueLabel(code=Decimal(7), label="Refused", is_sentinel=True),
        ],
    )


class FakeEmbedder:
    def encode_one(self, text: str) -> list[float]:
        del text
        return [0.0] * 768


@pytest.fixture
def pipeline(monkeypatch):
    """Patch the DB and every stage, recording the order stages are entered."""
    order: list[str] = []
    hits = [make_hit("FDSCAT4_A", 0.62), make_hit("FDSRUNOUT_A", 0.55)]

    state = {
        "intent": "codebook",
        "verdict": "MEASURED",
        "hits": hits,
        "logged": [],
        "survey_table_exists": False,
        "sql_result": None,
        "generated_sql": "SELECT 1",
    }

    async def fake_run_db(operation, *args, **kwargs):
        if operation is relational_store.ensure_conversation:
            return CONVERSATION_UUID
        if operation is relational_store.insert_message:
            return 42
        if operation is relational_store.load_history:
            return []
        if operation is vector_store.search_variables:
            order.append("retrieve")
            return list(state["hits"])
        if operation is vector_store.survey_table_exists:
            return state["survey_table_exists"]
        if operation is readonly_store.execute:
            order.append("execute")
            return state["sql_result"]
        if operation is relational_store.write_query_log:
            order.append("log")
            state["logged"].append(kwargs)
            return 1
        raise AssertionError(f"unexpected db operation: {operation}")

    async def fake_rewrite(question, history, *, gateway):
        del history, gateway
        order.append("rewrite")
        return question

    async def fake_route(question, *, gateway):
        del question, gateway
        order.append("route")
        return state["intent"]

    async def fake_embed(question, embedder):
        del question, embedder
        return [0.0] * 768

    async def fake_judge(question, hits_arg, *, gateway):
        del question, gateway
        order.append("judge")
        verdict = state["verdict"]
        closest = hits_arg[0] if hits_arg and verdict != "ABSENT" else None
        return verdict, closest, "because"

    async def fake_compose(question, hits_arg, verdict, *, gateway):
        del question, hits_arg, verdict, gateway
        order.append("generate")
        for token in ["Answer ", "about ", "FDSCAT4_A."]:
            yield token

    async def fake_sql_gen(question, survey_year, hits_arg, *, gateway):
        del question, survey_year, hits_arg, gateway
        order.append("sql_gen")
        return state["generated_sql"]

    monkeypatch.setattr(orch.db, "run_db", fake_run_db)
    monkeypatch.setattr(rewrite, "to_standalone", fake_rewrite)
    monkeypatch.setattr(router, "classify", fake_route)
    monkeypatch.setattr(retrieval, "embed_question", fake_embed)
    monkeypatch.setattr(judge, "classify_presence", fake_judge)
    monkeypatch.setattr(generate, "compose", fake_compose)
    monkeypatch.setattr(sql_gen, "generate", fake_sql_gen)

    return {"order": order, "state": state}


async def run(pipeline, question: str = "How is food insecurity measured?") -> list[dict]:
    orchestrator = orch.Orchestrator(
        gateway=FakeLLMGateway(responses=["unused"]), embedder=FakeEmbedder()
    )
    return [
        event
        async for event in orchestrator.handle_question(question, survey_year=2023)
    ]


def types_of(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


def answer_of(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "token")


# ---------------------------------------------------------------------------
# Property 10: Pipeline stage ordering
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("intent", ["codebook", "data", "both"])
@pytest.mark.parametrize("verdict", ["MEASURED", "RELATED", "ABSENT"])
async def test_property_10_pipeline_stage_ordering(pipeline, intent, verdict):
    """Feature: differ-platform, Property 10: Pipeline stage ordering.

    For all combinations of intent and verdict, the recorded sequence of stage
    invocations must match the fixed order defined for that combination.
    """
    pipeline["state"]["intent"] = intent
    pipeline["state"]["verdict"] = verdict

    events = await run(pipeline)
    order = pipeline["order"]

    # The stages that always run, always in this order.
    assert order[:4] == ["rewrite", "route", "retrieve", "judge"]
    # Telemetry is always last.
    assert order[-1] == "log"

    # Generation runs only where an LLM-composed answer is called for:
    # never for ABSENT (nothing to ground), and never for a data-intent
    # MEASURED turn (which returns the statistics limitation instead).
    expects_generate = verdict == "RELATED" or (
        verdict == "MEASURED" and intent == "codebook"
    )
    assert ("generate" in order) is expects_generate

    # Event order is fixed too: route, then verdict, then the rest.
    event_types = types_of(events)
    assert event_types[0] == "route"
    assert event_types[1] == "verdict"
    assert event_types[-1] == "done"


async def test_citations_always_precede_tokens(pipeline):
    """The UI renders citations above the answer, so they must arrive first."""
    pipeline["state"]["verdict"] = "MEASURED"
    pipeline["state"]["intent"] = "codebook"

    event_types = types_of(await run(pipeline))
    assert "citations" in event_types
    assert event_types.index("citations") < event_types.index("token")


# ---------------------------------------------------------------------------
# Property 3: Verdict-conditioned response shape
# ---------------------------------------------------------------------------
async def test_property_3_absent_carries_no_citation(pipeline):
    """Feature: differ-platform, Property 3: Verdict-conditioned response shape.

    If the verdict is ABSENT the response must never include a cited variable
    presented as the answer (Requirement 3.2).
    """
    pipeline["state"]["verdict"] = "ABSENT"
    events = await run(pipeline, "Does NHIS measure firearm ownership?")

    assert "citations" not in types_of(events)
    assert "Not measured in NHIS" in answer_of(events)


async def test_property_3_related_carries_exactly_the_top_candidate(pipeline):
    """Feature: differ-platform, Property 3: Verdict-conditioned response shape.

    If the verdict is RELATED the response must include exactly the single
    top-ranked candidate (Requirement 3.3).
    """
    pipeline["state"]["verdict"] = "RELATED"
    events = await run(pipeline, "Does NHIS measure loneliness?")

    citation_events = [e for e in events if e["type"] == "citations"]
    assert len(citation_events) == 1
    citations = citation_events[0]["citations"]
    assert len(citations) == 1
    assert citations[0]["variable_name"] == "FDSCAT4_A"


async def test_data_intent_without_loaded_microdata_returns_no_number(pipeline):
    """When the survey_YYYY table is absent, no number — an honest limitation."""
    pipeline["state"]["intent"] = "data"
    pipeline["state"]["verdict"] = "MEASURED"
    pipeline["state"]["survey_table_exists"] = False

    events = await run(pipeline, "What is the average BMI?")
    event_types = types_of(events)

    assert "result" not in event_types
    assert "sql" not in event_types
    assert "not loaded" in answer_of(events)


async def test_data_intent_with_failed_verification_shows_sql_but_no_number(pipeline):
    """Feature: differ-platform, Property 5: Verification gates numeric output.

    An invented code must produce a `sql` event tagged verified:false, a prose
    warning, and crucially NO `result` event and NO execution.
    """
    pipeline["state"]["intent"] = "data"
    pipeline["state"]["verdict"] = "MEASURED"
    pipeline["state"]["survey_table_exists"] = True
    # Code 99 is not defined on the fixture variable (only 1, 2, 7 are).
    pipeline["state"]["generated_sql"] = (
        "SELECT FDSCAT4_A, WTFA_A FROM survey_2023 WHERE FDSCAT4_A NOT IN (99)"
    )

    events = await run(pipeline, "What share are food insecure?")
    event_types = types_of(events)
    order = pipeline["order"]

    sql_event = next(e for e in events if e["type"] == "sql")
    assert sql_event["sql"]["verified"] is False
    assert sql_event["sql"]["problems"]
    assert "result" not in event_types
    assert "execute" not in order  # never executed
    assert "did not pass verification" in answer_of(events)
    assert pipeline["state"]["logged"][0]["sql_verified"] is False


async def test_data_intent_with_verified_sql_returns_a_result(pipeline):
    """Feature: differ-platform, Property 5: Verification gates numeric output.

    When the generated SQL references only defined codes and executes, a
    `result` event is emitted — this is the one path that yields a number.
    """
    pipeline["state"]["intent"] = "data"
    pipeline["state"]["verdict"] = "MEASURED"
    pipeline["state"]["survey_table_exists"] = True
    pipeline["state"]["generated_sql"] = (
        "SELECT FDSCAT4_A, WTFA_A FROM survey_2023 WHERE FDSCAT4_A IN (1, 2)"
    )
    pipeline["state"]["sql_result"] = readonly_store.SqlResult(
        columns=["fdscat4_a", "wtfa_a"], rows=[[1, 1234.5], [2, 987.0]]
    )

    events = await run(pipeline, "What share are food insecure?")
    event_types = types_of(events)
    order = pipeline["order"]

    sql_event = next(e for e in events if e["type"] == "sql")
    assert sql_event["sql"]["verified"] is True
    result_event = next(e for e in events if e["type"] == "result")
    assert result_event["result"]["columns"] == ["fdscat4_a", "wtfa_a"]
    assert result_event["result"]["weight_variable"] == "WTFA_A"
    assert event_types.index("sql") < event_types.index("result")
    assert "sql_gen" in order and "execute" in order
    assert pipeline["state"]["logged"][0]["sql_verified"] is True


# ---------------------------------------------------------------------------
# Property 1 / 2: grounding and citation completeness
# ---------------------------------------------------------------------------
async def test_property_2_citations_carry_all_four_fields(pipeline):
    """Feature: differ-platform, Property 2: Citation completeness."""
    events = await run(pipeline)
    citations = next(e for e in events if e["type"] == "citations")["citations"]

    for citation in citations:
        assert citation["variable_name"]
        assert citation["section"]
        assert isinstance(citation["page"], int)
        assert isinstance(citation["survey_year"], int)


async def test_emitted_citations_are_a_subset_of_retrieved(pipeline):
    """Feature: differ-platform, Property 1: Grounding filter.

    A variable that was never retrieved can never be cited.
    """
    events = await run(pipeline)
    citations = next(e for e in events if e["type"] == "citations")["citations"]
    retrieved = {hit.variable_name for hit in pipeline["state"]["hits"]}

    assert {c["variable_name"] for c in citations} <= retrieved


async def test_every_turn_writes_one_query_log_row(pipeline):
    """Requirement 17.1: one row per assistant turn, with latency."""
    await run(pipeline)
    logged = pipeline["state"]["logged"]

    assert len(logged) == 1
    assert logged[0]["verdict"] == "MEASURED"
    assert logged[0]["latency_ms"] is not None
    assert logged[0]["top_variable"] == "FDSCAT4_A"


async def test_gateway_failure_yields_error_and_logs_it(pipeline, monkeypatch):
    """A failed turn must surface an error and still be recorded."""
    from services.api.llm.gateway import LLMGatewayError

    async def exploding_route(question, *, gateway):
        del question, gateway
        raise LLMGatewayError("provider timeout")

    monkeypatch.setattr(router, "classify", exploding_route)

    events = await run(pipeline)
    event_types = types_of(events)

    assert "error" in event_types
    assert event_types[-1] == "done"
    logged = pipeline["state"]["logged"]
    assert len(logged) == 1
    assert logged[0]["verdict"] is None
    assert "provider timeout" in logged[0]["error"]
