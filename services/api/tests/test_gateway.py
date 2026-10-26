"""LLM Gateway tests (Requirements 14.1, 14.3), including Property 11."""

from __future__ import annotations

import pytest

from services.api.llm.gateway import (
    FakeLLMGateway,
    LLMGateway,
    LLMGatewayError,
    LLMResponse,
    Message,
)


def _msg(text: str) -> list[Message]:
    return [Message(role="user", content=text)]


async def test_scripted_responses_pop_in_order():
    gateway = FakeLLMGateway(responses=["first", "second"])
    assert (await gateway.complete(system="", messages=_msg("a"))).text == "first"
    assert (await gateway.complete(system="", messages=_msg("b"))).text == "second"


async def test_exhausted_script_raises_rather_than_returning_empty():
    """A stage silently receiving '' looks like a model fault; fail loudly."""
    gateway = FakeLLMGateway(responses=["only"])
    await gateway.complete(system="", messages=_msg("a"))
    with pytest.raises(LLMGatewayError):
        await gateway.complete(system="", messages=_msg("b"))


async def test_handler_mode_sees_stage():
    seen: list[str | None] = []

    def handler(system, messages, stage):
        seen.append(stage)
        return "ok"

    gateway = FakeLLMGateway(handler=handler)
    await gateway.complete(system="", messages=_msg("a"), stage="judge")
    assert seen == ["judge"]


async def test_stream_yields_incrementally_and_reassembles():
    gateway = FakeLLMGateway(responses=["alpha beta gamma"])
    chunks = [
        chunk async for chunk in gateway.stream(system="", messages=_msg("a"))
    ]
    assert len(chunks) > 1
    assert "".join(chunks) == "alpha beta gamma"


async def test_records_calls_for_assertions():
    gateway = FakeLLMGateway(responses=["x"])
    await gateway.complete(system="sys", messages=_msg("q"), stage="router")
    assert gateway.calls[0]["stage"] == "router"
    assert gateway.calls[0]["system"] == "sys"


def test_fake_satisfies_the_protocol():
    assert isinstance(FakeLLMGateway(responses=["x"]), LLMGateway)


# ---------------------------------------------------------------------------
# Property 11: LLM Gateway interface conformance
# ---------------------------------------------------------------------------
async def test_property_11_llm_gateway_interface_conformance():
    """Feature: differ-platform, Property 11: LLM Gateway interface conformance.

    For at least two fake LLMGateway implementations with different internal
    behavior, calling the same pipeline-stage function through each must
    produce a response of the expected shape without the stage's code
    branching on which implementation is in use.
    """

    class ReversingGateway:
        """A second implementation with entirely different internals."""

        model = "reversing"

        async def complete(
            self, *, system, messages, temperature=0.0,
            response_format="text", stage=None,
        ):
            del system, temperature, response_format, stage
            return LLMResponse(text=messages[-1].content[::-1], model=self.model)

        async def stream(self, *, system, messages, temperature=0.0, stage=None):
            del system, temperature, stage
            for char in messages[-1].content[::-1]:
                yield char

    async def call_stage(gateway) -> LLMResponse:
        """Stage code written once, with no knowledge of the implementation."""
        return await gateway.complete(
            system="you are a stage", messages=_msg("abc"), stage="judge"
        )

    implementations = [FakeLLMGateway(responses=["scripted"]), ReversingGateway()]

    for gateway in implementations:
        response = await call_stage(gateway)
        assert isinstance(response, LLMResponse)
        assert isinstance(response.text, str)
        assert response.text
        assert isinstance(response.model, str)


async def test_property_11_streaming_shape_is_implementation_independent():
    class CharGateway:
        model = "char"

        async def complete(self, **kwargs):
            return LLMResponse(text="x", model=self.model)

        async def stream(self, *, system, messages, temperature=0.0, stage=None):
            del system, temperature, stage
            for char in messages[-1].content:
                yield char

    async def consume(gateway) -> str:
        return "".join(
            [c async for c in gateway.stream(system="", messages=_msg("hello"))]
        )

    assert await consume(CharGateway()) == "hello"
    assert await consume(FakeLLMGateway(responses=["hello"])) == "hello"
