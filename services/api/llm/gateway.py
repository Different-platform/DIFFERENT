"""LLM access behind one narrow interface (Requirement 14.1, 14.2, 14.3).

Every pipeline stage calls through `LLMGateway`. This is the only module in
the codebase permitted to import the Groq SDK, so swapping providers means
writing one new class and changing one line of configuration — no pipeline
stage changes.

It also makes every stage testable without a network call, which is how the
pipeline-ordering property and the eval harness avoid burning quota.

Deviation from design.md: `complete()`/`stream()` take an optional `stage`
label. It is additive and earns its place three times over — it lets the fake
gateway return stage-appropriate output, it gives telemetry a way to attribute
latency per stage, and it makes logs readable when four LLM calls serve one
question.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]
ResponseFormat = Literal["text", "json"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str


class LLMGatewayError(RuntimeError):
    """Raised when the provider fails (timeout, quota, malformed response).

    The orchestrator converts this into a visible error event and an error
    row in query_log rather than logging the turn as if it had succeeded.
    """


@runtime_checkable
class LLMGateway(Protocol):
    """The contract every pipeline stage depends on."""

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        response_format: ResponseFormat = "text",
        stage: str | None = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        stage: str | None = None,
    ) -> AsyncIterator[str]: ...


class GroqGateway:
    """Groq-backed implementation. The sole importer of the Groq SDK."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 60.0):
        if not api_key:
            raise ValueError("GroqGateway requires an API key")
        # Imported lazily so that a deployment running the fake gateway does
        # not need the SDK present at import time.
        from groq import AsyncGroq

        self._client = AsyncGroq(api_key=api_key, timeout=timeout_seconds)
        self.model = model

    def _payload(self, system: str, messages: list[Message]) -> list[dict[str, str]]:
        payload = [{"role": "system", "content": system}] if system else []
        payload.extend({"role": m.role, "content": m.content} for m in messages)
        return payload

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        response_format: ResponseFormat = "text",
        stage: str | None = None,
    ) -> LLMResponse:
        del stage  # used only by the fake gateway and logging
        kwargs: dict = {
            "model": self.model,
            "messages": self._payload(system, messages),
            "temperature": temperature,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # provider errors are not ours to classify
            raise LLMGatewayError(f"Groq completion failed: {exc}") from exc

        choice = completion.choices[0].message.content or ""
        return LLMResponse(text=choice, model=self.model)

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        stage: str | None = None,
    ) -> AsyncIterator[str]:
        del stage
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=self._payload(system, messages),
                temperature=temperature,
                stream=True,
            )
            async for chunk in response:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            raise LLMGatewayError(f"Groq stream failed: {exc}") from exc


#: Signature of a fake gateway's response policy.
FakeHandler = Callable[[str, list["Message"], str | None], str]


class FakeLLMGateway:
    """Deterministic gateway for tests and for running with no API key.

    Two modes:

    * `responses=[...]` — pops scripted replies in order. Deterministic and
      explicit, which is what property tests over pipeline ordering need.
    * `handler=fn` — computes a reply from (system, messages, stage), for
      exercising stage-appropriate behaviour end to end.

    Exhausting a scripted list raises rather than silently returning empty
    text, because a stage quietly receiving "" is exactly the kind of failure
    that looks like a model problem and wastes an afternoon.
    """

    model = "fake"

    def __init__(
        self,
        responses: list[str] | None = None,
        handler: FakeHandler | None = None,
    ):
        if responses is None and handler is None:
            raise ValueError("FakeLLMGateway needs either responses or a handler")
        self._responses = list(responses or [])
        self._handler = handler
        self.calls: list[dict] = []

    def _next_text(self, system: str, messages: list[Message], stage: str | None) -> str:
        self.calls.append({"system": system, "messages": messages, "stage": stage})
        if self._handler is not None:
            return self._handler(system, messages, stage)
        if not self._responses:
            raise LLMGatewayError(
                f"FakeLLMGateway ran out of scripted responses "
                f"(stage={stage!r}, {len(self.calls)} calls made)"
            )
        return self._responses.pop(0)

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        response_format: ResponseFormat = "text",
        stage: str | None = None,
    ) -> LLMResponse:
        del temperature, response_format
        return LLMResponse(
            text=self._next_text(system, messages, stage), model=self.model
        )

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        stage: str | None = None,
    ) -> AsyncIterator[str]:
        del temperature
        text = self._next_text(system, messages, stage)
        # Chunk on whitespace so consumers exercise real incremental assembly.
        for token in _split_preserving_whitespace(text):
            yield token


def _split_preserving_whitespace(text: str) -> list[str]:
    import re

    return [part for part in re.split(r"(\s+)", text) if part]


def build_gateway() -> LLMGateway:
    """Pick a gateway from configuration (Requirement 14.2).

    Falls back to the fake gateway when no key is configured so that the API
    still starts and every path except real generation can be exercised. The
    fallback is loud at startup rather than silent at request time.
    """
    from services.api.config import settings
    from services.api.llm.stub_policy import stub_handler

    if settings.has_real_llm:
        return GroqGateway(api_key=settings.groq_api_key, model=settings.groq_model)
    return FakeLLMGateway(handler=stub_handler)
