"""The fixed pipeline (Requirement 15.1, 15.2, 15.3).

Every question travels the same ordered stages:

    rewrite -> route -> retrieve -> judge -> (answer | statistics) -> log

Branching is a plain `if/else` on `intent` and `verdict`. There is no planner
and no agent choosing its own next action, and nothing here imports CrewAI,
LangChain, or LangGraph. That is not stylistic preference: if the same
question could take different paths on different runs, the eval baseline
would stop being comparable and we would lose the ability to tell whether a
change helped.

The `data` intent is deliberately incomplete in this version. SQL generation,
verification against codebook codes, and the weighted-statistics engine are a
later phase, so a question asking for a number gets an explicit statement of
that limitation. Returning an unverified figure instead would invert the one
promise the product makes.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator
from uuid import UUID

from services.api import events
from services.api.domain import Citation, Hit, RouteIntent, Verdict
from services.api.llm.gateway import LLMGateway, LLMGatewayError
from services.api.rag import (
    derivations,
    generate,
    judge,
    present,
    retrieval,
    rewrite,
    router,
    scope,
    sql_gen,
    verify,
)
from services.api.store import db, readonly_store, relational_store, vector_store

logger = logging.getLogger(__name__)

RETRIEVAL_LIMIT = 8

#: Statistics questions retrieve a few more candidates. A derived measure such
#: as BMI needs every input variable (height AND weight) to be retrieved, and a
#: lower-ranked input silently turns "computable" into "not measured".
RETRIEVAL_LIMIT_DATA = 10

#: Extra attempts to repair SQL the verifier rejected. The repaired query must
#: still pass the verifier; this only buys a second chance at a valid one.
MAX_SQL_REPAIRS = 1


class Orchestrator:
    """Holds the long-lived dependencies a request needs.

    The embedding model is injected rather than constructed per request: it is
    ~420 MB and must stay resident for the process lifetime (Requirement
    13.2), and it must be the same model used at ingestion (Requirement 13.3).
    """

    def __init__(self, *, gateway: LLMGateway, embedder):
        self.gateway = gateway
        self.embedder = embedder

    async def handle_question(
        self,
        question: str,
        *,
        conversation_id: UUID | str | None = None,
        survey_year: int | None = None,
    ) -> AsyncIterator[dict]:
        """Run the pipeline, yielding SSE event payloads in fixed order."""
        started = time.perf_counter()

        standalone = question
        intent: RouteIntent | None = None
        verdict: Verdict | None = None
        hits: list[Hit] = []
        citations: list[Citation] = []
        answer_parts: list[str] = []
        assistant_message_id: int | None = None
        conversation_uuid = None
        # Populated only on the verified-statistics path, for query_log.
        sql_state: dict = {"sql": None, "verified": None, "notes": None}

        try:
            # --- persist the turn so history survives a reload ---
            conversation_uuid = await db.run_db(
                relational_store.ensure_conversation,
                conversation_id,
                default_year=survey_year,
                title=question[:120],
            )
            # Read history BEFORE saving this question, so the turn being asked
            # can never appear in its own context. (The old approach saved
            # first and then filtered by text, which also threw away any
            # earlier turn that happened to repeat the same words.)
            history = await db.run_db(relational_store.load_history, conversation_uuid)
            await db.run_db(
                relational_store.insert_message,
                conversation_uuid,
                role="user",
                content=question,
            )

            # --- stage 1: rewrite follow-ups into a standalone question ---
            standalone = await rewrite.to_standalone(
                question, history, gateway=self.gateway
            )

            # --- stage 2: route ---
            intent = await router.classify(standalone, gateway=self.gateway)
            yield events.route(intent)

            # A year named in the question beats the selector; a statistics
            # question with neither uses the latest year rather than mixing
            # years (and says so in the answer).
            survey_year, year_assumed = scope.resolve_year(
                standalone, survey_year, intent
            )

            # --- stage 3: retrieve ---
            vector = await retrieval.embed_question(standalone, self.embedder)
            hits = await db.run_db(
                vector_store.search_variables,
                vector,
                survey_year=survey_year,
                limit=(
                    RETRIEVAL_LIMIT_DATA
                    if intent in ("data", "both")
                    else RETRIEVAL_LIMIT
                ),
            )
            hits = retrieval.dedupe_by_variable(hits)
            if intent in ("data", "both") and survey_year is not None:
                hits = await self._with_derivation_inputs(
                    standalone, hits, survey_year
                )

            # --- stage 4: presence judge ---
            verdict, closest, why = await judge.classify_presence(
                standalone, hits, gateway=self.gateway
            )
            yield events.verdict(verdict)

            # --- stage 5: answer, branching on verdict then intent ---
            if verdict == "ABSENT":
                # No citations: offering a variable here is exactly the
                # mistake the judge exists to prevent (Requirement 3.2).
                text = generate.not_measured_message(standalone, why)
                for chunk in _chunk(text):
                    answer_parts.append(chunk)
                    yield events.token(chunk)

            elif verdict == "RELATED":
                # Exactly the closest variable, with an explicit caveat
                # (Requirement 3.3).
                citations = [closest.to_citation()] if closest is not None else []
                if citations:
                    yield events.citations(citations)
                async for chunk in generate.compose(
                    standalone, hits[:1], verdict, gateway=self.gateway
                ):
                    answer_parts.append(chunk)
                    yield events.token(chunk)

            else:  # MEASURED
                citations = generate.citations_from_hits(hits)
                yield events.citations(citations)

                if intent in ("data", "both"):
                    async for event, chunk in self._verified_statistics(
                        standalone, hits, survey_year, sql_state, year_assumed
                    ):
                        if chunk is not None:
                            answer_parts.append(chunk)
                        yield event
                    # Persist the variables the query actually used, not just
                    # the top retrieval hits.
                    citations = sql_state.get("citations") or citations
                else:
                    async for chunk in generate.compose(
                        standalone, hits, verdict, gateway=self.gateway
                    ):
                        answer_parts.append(chunk)
                        yield events.token(chunk)

            answer = "".join(answer_parts)

            assistant_message_id = await db.run_db(
                relational_store.insert_message,
                conversation_uuid,
                role="assistant",
                content=answer,
                citations=citations or None,
                verdict=verdict,
            )

            await self._log(
                question=question,
                standalone=standalone,
                conversation_uuid=conversation_uuid,
                message_id=assistant_message_id,
                intent=intent,
                verdict=verdict,
                hits=hits,
                started=started,
                sql_state=sql_state,
            )

            yield events.done(assistant_message_id)

        except LLMGatewayError as exc:
            logger.exception("LLM gateway failed")
            await self._log_error(
                question, standalone, conversation_uuid, intent, hits, started, str(exc)
            )
            yield events.error(
                "The language model did not respond, so this question could "
                "not be answered. Nothing was guessed."
            )
            yield events.done(None)

        except Exception as exc:  # noqa: BLE001 - surface, log, never hang
            logger.exception("pipeline failed")
            await self._log_error(
                question, standalone, conversation_uuid, intent, hits, started, str(exc)
            )
            yield events.error(f"This question could not be processed: {exc}")
            yield events.done(None)

    async def _with_derivation_inputs(
        self, question: str, hits: list[Hit], survey_year: int
    ) -> list[Hit]:
        """Add the inputs of a known derived measure (BMI -> height, weight).

        Appended at the end with the lowest score already present, so they are
        visible to the judge and the SQL generator without outranking what
        retrieval actually found (a fetched variable would otherwise carry a
        meaningless similarity of 1.0).
        """
        present_names = {hit.variable_name for hit in hits}
        floor = min((hit.similarity for hit in hits), default=0.0)
        extra: list[Hit] = []
        for name in derivations.required_inputs(question):
            if name in present_names:
                continue
            found = await db.run_db(vector_store.get_variable, survey_year, name)
            if found is not None:
                found.similarity = floor
                extra.append(found)
        return hits + extra

    async def _verified_statistics(
        self,
        question: str,
        hits: list[Hit],
        survey_year: int | None,
        sql_state: dict,
        year_assumed: bool = False,
    ) -> AsyncIterator[tuple[dict, str | None]]:
        """The verified-statistics path: generate -> verify -> execute -> compute.

        Yields (event, answer_chunk) pairs; `answer_chunk` is the text to
        accumulate into the stored answer, or None for non-token events.

        The one rule that governs every branch: a number is emitted only after
        `verify.check()` returns no problems AND read-only execution succeeds.
        Every other path yields prose and no `result` event.
        """
        if survey_year is None:
            text = generate.statistics_unavailable_message(hits)
            for chunk in _chunk(text):
                yield events.token(chunk), chunk
            return

        has_table = await db.run_db(vector_store.survey_table_exists, survey_year)
        if not has_table:
            text = generate.statistics_unavailable_message(hits)
            for chunk in _chunk(text):
                yield events.token(chunk), chunk
            return

        # --- generate (codes injected, not recalled) and verify ---
        # The verifier is the gate. If it rejects the first attempt, the
        # model gets the reasons once and tries again; whatever is finally
        # shown and run is whichever SQL the verifier last judged.
        codebook = verify.codebook_from_hits(hits, survey_year)
        sentinels = verify.sentinels_from_hits(hits, survey_year)

        sql = ""
        problems: list[verify.Problem] = []
        feedback: list[str] | None = None
        for attempt in range(1 + MAX_SQL_REPAIRS):
            sql = await sql_gen.generate(
                question, survey_year, hits, gateway=self.gateway, feedback=feedback
            )
            problems = verify.check(sql, codebook, sentinels)
            if not problems:
                break
            feedback = [p.message for p in problems]
            logger.info(
                "SQL attempt %d rejected by the verifier: %s", attempt + 1, feedback
            )

        verified = not problems
        sql_state["sql"] = sql
        sql_state["verified"] = verified
        sql_state["notes"] = [p.to_dict() for p in problems]

        # Cite the variables the query actually used. Retrieval ranking is a
        # guess about relevance; the SQL is what produced the number.
        used = present.variables_in(sql, hits)
        if used:
            cited = [hit.to_citation() for hit in used]
            sql_state["citations"] = cited
            yield events.citations(cited), None

        yield (
            events.sql(sql, verified=verified, problems=[p.message for p in problems]),
            None,
        )

        if problems:
            text = generate.verification_failed_message(problems)
            for chunk in _chunk(text):
                yield events.token(chunk), chunk
            return

        # --- execute under differ_ro, read-only ---
        try:
            result = await db.run_db(readonly_store.execute, sql)
        except readonly_store.ReadOnlyExecutionError as exc:
            logger.warning("verified SQL failed to execute: %s", exc)
            sql_state["notes"] = (sql_state["notes"] or []) + [
                {"kind": "execution_error", "message": str(exc)}
            ]
            text = generate.execution_failed_message()
            for chunk in _chunk(text):
                yield events.token(chunk), chunk
            return

        # An average over nobody is NULL. Never present that as a figure.
        if present.is_empty(result.rows):
            text = generate.empty_result_message(survey_year)
            for chunk in _chunk(text):
                yield events.token(chunk), chunk
            return

        # --- label the codes, format the numbers, describe what was computed ---
        presented = present.present_result(
            result.columns,
            result.rows,
            sql=sql,
            hits=hits,
            year=survey_year,
            year_assumed=year_assumed,
        )
        yield (
            events.result(presented.columns, presented.rows, sql_gen.WEIGHT_COLUMN),
            None,
        )
        text = presented.summary
        if result.truncated:
            text += f" Only the first {readonly_store.MAX_ROWS} rows are shown."
        for chunk in _chunk(text):
            yield events.token(chunk), chunk

    async def _log(
        self,
        *,
        question: str,
        standalone: str,
        conversation_uuid,
        message_id: int | None,
        intent: RouteIntent | None,
        verdict: Verdict | None,
        hits: list[Hit],
        started: float,
        sql_state: dict | None = None,
    ) -> None:
        sql_state = sql_state or {}
        await db.run_db(
            relational_store.write_query_log,
            question=question,
            conversation_id=conversation_uuid,
            message_id=message_id,
            rewritten_question=standalone if standalone != question else None,
            intent=intent,
            verdict=verdict,
            top_variable=hits[0].variable_name if hits else None,
            top_similarity=hits[0].similarity if hits else None,
            generated_sql=sql_state.get("sql"),
            sql_verified=sql_state.get("verified"),
            verification_notes=sql_state.get("notes") or None,
            retrieved=_retrieved_summary(hits),
            latency_ms=_elapsed_ms(started),
        )

    async def _log_error(
        self,
        question: str,
        standalone: str,
        conversation_uuid,
        intent: RouteIntent | None,
        hits: list[Hit],
        started: float,
        error: str,
    ) -> None:
        """Record a failed turn with verdict NULL (see relational_store)."""
        try:
            await db.run_db(
                relational_store.write_query_log,
                question=question,
                conversation_id=conversation_uuid,
                rewritten_question=standalone if standalone != question else None,
                intent=intent,
                verdict=None,
                top_variable=hits[0].variable_name if hits else None,
                top_similarity=hits[0].similarity if hits else None,
                retrieved=_retrieved_summary(hits),
                error=error[:2000],
                latency_ms=_elapsed_ms(started),
            )
        except Exception:  # noqa: BLE001
            # Telemetry must never mask the original failure.
            logger.exception("failed to write the error row to query_log")


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _retrieved_summary(hits: list[Hit]) -> list[dict]:
    return [
        {
            "variable_name": hit.variable_name,
            "score": round(hit.similarity, 4),
            "section": hit.section_name,
            "page": hit.page,
        }
        for hit in hits
    ]


def _chunk(text: str) -> list[str]:
    """Split locally-composed text so it streams like generated text.

    Without this an ABSENT answer would arrive as one block while a MEASURED
    answer trickles in, and the UI's typing cursor would behave differently
    depending on the verdict.
    """
    return [part for part in re.split(r"(\s+)", text) if part]
