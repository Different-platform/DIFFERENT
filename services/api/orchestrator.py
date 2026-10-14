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
from services.api.rag import generate, judge, retrieval, rewrite, router
from services.api.store import db, relational_store, vector_store

logger = logging.getLogger(__name__)

RETRIEVAL_LIMIT = 8


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

        try:
            # --- persist the turn so history survives a reload ---
            conversation_uuid = await db.run_db(
                relational_store.ensure_conversation,
                conversation_id,
                default_year=survey_year,
                title=question[:120],
            )
            await db.run_db(
                relational_store.insert_message,
                conversation_uuid,
                role="user",
                content=question,
            )
            history = await db.run_db(relational_store.load_history, conversation_uuid)
            # Drop the turn just inserted; it is the question being asked.
            history = [m for m in history if m.content != question] or []

            # --- stage 1: rewrite follow-ups into a standalone question ---
            standalone = await rewrite.to_standalone(
                question, history, gateway=self.gateway
            )

            # --- stage 2: route ---
            intent = await router.classify(standalone, gateway=self.gateway)
            yield events.route(intent)

            # --- stage 3: retrieve ---
            vector = await retrieval.embed_question(standalone, self.embedder)
            hits = await db.run_db(
                vector_store.search_variables,
                vector,
                survey_year=survey_year,
                limit=RETRIEVAL_LIMIT,
            )
            hits = retrieval.dedupe_by_variable(hits)

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
                    text = generate.statistics_unavailable_message(hits)
                    for chunk in _chunk(text):
                        answer_parts.append(chunk)
                        yield events.token(chunk)
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
    ) -> None:
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
