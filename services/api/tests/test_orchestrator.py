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
from services.api.llm.gateway import Message
from services.api.rag import (
    explain,
    followup,
    generate,
    judge,
    retrieval,
    router,
    rewrite,
    sql_gen,
)
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
    db_ops: list[str] = []  # kept apart from `order`, which Property 10 pins
    hits = [make_hit("FDSCAT4_A", 0.62), make_hit("FDSRUNOUT_A", 0.55)]

    state = {
        "intent": "codebook",
        "verdict": "MEASURED",
        "hits": hits,
        "logged": [],
        "survey_table_exists": False,
        "sql_result": None,
        "generated_sql": "SELECT 1",
        "searched_year": "unset",
        "fetched": [],
        "fetchable": {},
        "sql_gen_hits": None,
        "history": [],
        "prior": None,
        "asks_explain": False,
        "classifier_calls": 0,
    }

    async def fake_asks_for_explanation(question, prior_q, prior_a, *, gateway):
        del question, prior_q, prior_a, gateway
        state["classifier_calls"] += 1
        return state["asks_explain"]

    async def fake_explain_compose(**kwargs):
        order.append("explain")
        return "It is 703 times weight over height squared."

    async def fake_run_db(operation, *args, **kwargs):
        if operation is relational_store.ensure_conversation:
            return CONVERSATION_UUID
        if operation is relational_store.insert_message:
            db_ops.append(f"insert_{kwargs.get('role')}")
            return 42
        if operation is relational_store.load_history:
            db_ops.append("load_history")
            return list(state["history"])
        if operation is relational_store.load_last_assistant:
            return state["prior"]
        if operation is vector_store.search_variables:
            order.append("retrieve")
            state["searched_year"] = kwargs.get("survey_year")
            return list(state["hits"])
        if operation is vector_store.survey_table_exists:
            return state["survey_table_exists"]
        if operation is vector_store.get_variable:
            name = args[1]
            state["fetched"].append(name)
            return state["fetchable"].get(name)
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

    async def fake_sql_gen(question, survey_year, hits_arg, *, gateway, feedback=None):
        del question, survey_year, gateway, feedback
        state["sql_gen_hits"] = [h.variable_name for h in hits_arg]
        order.append("sql_gen")
        return state["generated_sql"]

    monkeypatch.setattr(orch.db, "run_db", fake_run_db)
    monkeypatch.setattr(rewrite, "to_standalone", fake_rewrite)
    monkeypatch.setattr(router, "classify", fake_route)
    monkeypatch.setattr(retrieval, "embed_question", fake_embed)
    monkeypatch.setattr(judge, "classify_presence", fake_judge)
    monkeypatch.setattr(generate, "compose", fake_compose)
    monkeypatch.setattr(sql_gen, "generate", fake_sql_gen)
    monkeypatch.setattr(
        followup, "asks_for_explanation", fake_asks_for_explanation
    )
    monkeypatch.setattr(explain, "compose", fake_explain_compose)

    return {"order": order, "state": state, "db_ops": db_ops}


async def run(
    pipeline,
    question: str = "How is food insecurity measured?",
    survey_year: int | None = 2023,
) -> list[dict]:
    orchestrator = orch.Orchestrator(
        gateway=FakeLLMGateway(responses=["unused"]), embedder=FakeEmbedder()
    )
    return [
        event
        async for event in orchestrator.handle_question(
            question, survey_year=survey_year
        )
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
    # Codes are labelled for the reader ("Yes (1)", not a bare 1).
    assert result_event["result"]["columns"][0] == "FDSCAT4_A"
    assert [row[0] for row in result_event["result"]["rows"]] == ["Yes (1)", "No (2)"]
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


# ---------------------------------------------------------------------------
# Conversation memory and year handling
# ---------------------------------------------------------------------------
async def test_history_is_read_before_the_question_is_saved(pipeline):
    """A follow-up is rewritten from *earlier* turns, never from itself."""
    await run(pipeline)
    ops = pipeline["db_ops"]
    assert ops.index("load_history") < ops.index("insert_user")


async def test_year_named_in_the_question_beats_the_selector(pipeline):
    pipeline["state"]["intent"] = "codebook"
    await run(pipeline, "How is food insecurity measured in 2021?", survey_year=2023)
    assert pipeline["state"]["searched_year"] == 2021


async def test_statistics_question_without_a_year_uses_the_latest(pipeline):
    pipeline["state"]["intent"] = "data"
    await run(pipeline, "What is the average age of adults?", survey_year=None)
    assert pipeline["state"]["searched_year"] == 2023


async def test_codebook_question_without_a_year_stays_cross_year(pipeline):
    pipeline["state"]["intent"] = "codebook"
    await run(pipeline, "How is food insecurity measured?", survey_year=None)
    assert pipeline["state"]["searched_year"] is None


async def test_rejected_sql_gets_one_repair_attempt(pipeline):
    """A first attempt that fails verification is retried once with feedback."""
    state = pipeline["state"]
    state["intent"] = "data"
    state["survey_table_exists"] = True
    # Always invalid (99 is not a code of the fixture variable), so both
    # attempts are rejected: exactly two generations, never an execution.
    state["generated_sql"] = (
        "SELECT FDSCAT4_A, WTFA_A FROM survey_2023 WHERE FDSCAT4_A NOT IN (99)"
    )
    await run(pipeline, "What share are food insecure?")
    order = pipeline["order"]
    assert order.count("sql_gen") == 2
    assert "execute" not in order


async def test_empty_result_is_never_presented_as_a_number(pipeline):
    state = pipeline["state"]
    state["intent"] = "data"
    state["survey_table_exists"] = True
    state["generated_sql"] = (
        "SELECT SUM(FDSCAT4_A * WTFA_A) / SUM(WTFA_A) AS weighted_mean "
        "FROM survey_2023 WHERE FDSCAT4_A NOT IN (7)"
    )
    state["sql_result"] = readonly_store.SqlResult(
        columns=["weighted_mean"], rows=[[None]]
    )
    events = await run(pipeline, "What is the average food security?")
    assert "result" not in types_of(events)
    assert "matched no respondents" in answer_of(events)


async def test_bmi_statistics_question_pulls_in_height_and_weight(pipeline):
    """BMI is computable only if both inputs are candidates, whatever retrieval ranked."""
    state = pipeline["state"]
    state["intent"] = "data"
    state["survey_table_exists"] = True
    state["fetchable"] = {
        "HEIGHTTC_A": make_hit("HEIGHTTC_A", 1.0),
        "WEIGHTLBTC_A": make_hit("WEIGHTLBTC_A", 1.0),
    }
    state["generated_sql"] = "SELECT 1"
    events = await run(pipeline, "What is the average body mass index of adults?")
    assert sorted(state["fetched"]) == ["HEIGHTTC_A", "WEIGHTLBTC_A"]
    assert {"HEIGHTTC_A", "WEIGHTLBTC_A"} <= set(state["sql_gen_hits"] or [])
    # With both inputs present the verdict comes from the derivation table, not
    # from a model call that can flip between runs on identical wording.
    assert "judge" not in pipeline["order"]
    assert next(e for e in events if e["type"] == "verdict")["verdict"] == "MEASURED"


async def test_missing_derivation_input_leaves_the_decision_to_the_judge(pipeline):
    """If the file lacks one input, 'computable' is not established."""
    state = pipeline["state"]
    state["intent"] = "data"
    state["fetchable"] = {"WEIGHTLBTC_A": make_hit("WEIGHTLBTC_A", 1.0)}  # no height
    state["verdict"] = "RELATED"
    events = await run(pipeline, "What is the average BMI?")
    assert "judge" in pipeline["order"]
    assert next(e for e in events if e["type"] == "verdict")["verdict"] == "RELATED"


async def test_non_derived_data_question_still_uses_the_judge(pipeline):
    pipeline["state"]["intent"] = "data"
    await run(pipeline, "What is the average age of adults?")
    assert "judge" in pipeline["order"]


async def test_derivation_inputs_are_not_fetched_when_already_retrieved(pipeline):
    state = pipeline["state"]
    state["intent"] = "data"
    state["hits"] = [make_hit("HEIGHTTC_A", 0.4), make_hit("WEIGHTLBTC_A", 0.5)]
    await run(pipeline, "What is the average BMI?")
    assert state["fetched"] == []


async def test_codebook_question_about_bmi_does_not_fetch_derivation_inputs(pipeline):
    pipeline["state"]["intent"] = "codebook"
    await run(pipeline, "How is BMI measured?")
    assert pipeline["state"]["fetched"] == []


# ---------------------------------------------------------------------------
# "Explain that" follow-ups answer from the computation that ran
# ---------------------------------------------------------------------------
PRIOR_SQL = (
    "SELECT SUM(703.0 * WEIGHTLBTC_A / (HEIGHTTC_A * HEIGHTTC_A) * WTFA_A) / "
    "SUM(WTFA_A) AS weighted_mean_bmi FROM survey_2023 WHERE HEIGHTTC_A NOT IN "
    "(96) AND WEIGHTLBTC_A NOT IN (996)"
)


def computed_turn(state, with_sql=True):
    state["history"] = [
        Message(role="user", content="What is the weighted mean BMI in 2023?"),
        Message(role="assistant", content="The weighted mean is 28.03."),
    ]
    state["prior"] = {
        "id": 1,
        "content": "The weighted mean is 28.03.",
        "citations": [
            {
                "variable_name": "HEIGHTTC_A",
                "section": "Height",
                "page": 3,
                "survey_year": 2023,
                "description": "Height",
                "question_text": None,
            }
        ],
        "verdict": "MEASURED",
        "generated_sql": PRIOR_SQL if with_sql else None,
        "sql_verified": True if with_sql else None,
    }


async def test_explain_request_is_answered_from_the_saved_computation(pipeline):
    state = pipeline["state"]
    computed_turn(state)
    state["asks_explain"] = True

    events = await run(pipeline, "Explain me in detail")
    kinds = types_of(events)
    order = pipeline["order"]

    # It never goes back through retrieval or the judge, which cannot see the
    # computation and previously contradicted it ("BMI cannot be computed").
    assert "retrieve" not in order and "judge" not in order
    assert "sql_gen" not in order and "execute" not in order
    assert "explain" in order
    # The SQL it describes is shown again, verified; no new number is produced.
    sql_event = next(e for e in events if e["type"] == "sql")
    assert sql_event["sql"]["sql"] == PRIOR_SQL and sql_event["sql"]["verified"] is True
    assert "result" not in kinds
    assert "703 times weight" in answer_of(events)
    assert kinds[0] == "route" and kinds[-1] == "done"
    # The turn is logged as an explanation, once.
    assert len(state["logged"]) == 1 and state["logged"][0]["intent"] == "explain"


async def test_a_non_explain_follow_up_takes_the_normal_pipeline(pipeline):
    state = pipeline["state"]
    computed_turn(state)
    state["asks_explain"] = False

    await run(pipeline, "What about 2021?")
    assert state["classifier_calls"] == 1
    assert "retrieve" in pipeline["order"] and "explain" not in pipeline["order"]


async def test_classifier_is_not_called_when_the_last_answer_has_no_computation(pipeline):
    """Nothing to explain after a codebook answer; don't spend a call asking."""
    state = pipeline["state"]
    computed_turn(state, with_sql=False)
    state["asks_explain"] = True

    await run(pipeline, "Explain me in detail")
    assert state["classifier_calls"] == 0
    assert "explain" not in pipeline["order"]


async def test_classifier_is_not_called_on_the_first_message(pipeline):
    await run(pipeline, "How is food insecurity measured?")
    assert pipeline["state"]["classifier_calls"] == 0


async def test_usage_limit_is_reported_as_such_with_the_wait(pipeline, monkeypatch):
    """'The language model did not respond' hid a spent quota behind a vague error."""
    from services.api.llm.gateway import LLMRateLimitError

    async def limited_route(question, *, gateway):
        del question, gateway
        raise LLMRateLimitError("cap", retry_after_seconds=582, daily=True)

    monkeypatch.setattr(router, "classify", limited_route)

    events = await run(pipeline)
    error = next(e for e in events if e["type"] == "error")["message"]
    assert "daily usage limit" in error
    assert "about 10 minutes" in error
    assert "did not respond" not in error
    assert types_of(events)[-1] == "done"
    # Recorded as a failed turn, not as if it had succeeded.
    assert pipeline["state"]["logged"][0]["verdict"] is None
