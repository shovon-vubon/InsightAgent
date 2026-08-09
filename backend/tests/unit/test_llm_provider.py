"""Behaviour of the provider base class: retry, classification, streaming contract.

Exercised through `FakeProvider`, so these assertions hold for every provider that
inherits the base — which is the point of putting retry there rather than in each
SDK wrapper.
"""

from __future__ import annotations

import pytest

from app.llm.base import LLMProvider, Message, Role, StreamFinished, TextDelta
from app.llm.errors import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    is_retryable,
)
from app.llm.fake import FakeProvider

QUESTION = [Message(role=Role.USER, content="why did revenue fall")]


async def collect(provider: LLMProvider, messages: list[Message] = QUESTION) -> tuple[str, object]:
    text: list[str] = []
    finished: object = None
    async for event in provider.stream(messages):
        if isinstance(event, TextDelta):
            text.append(event.text)
        else:
            finished = event
    return "".join(text), finished


class TestErrorClassification:
    @pytest.mark.parametrize(
        "error",
        [LLMRateLimitError(), LLMProviderError("unclassified transport failure")],
    )
    def test_transient_failures_are_retryable(self, error: LLMProviderError) -> None:
        assert is_retryable(error)

    @pytest.mark.parametrize("error", [LLMAuthenticationError(), LLMBadRequestError()])
    def test_permanent_failures_are_not(self, error: LLMProviderError) -> None:
        assert not is_retryable(error)


class TestCompleteRetry:
    async def test_recovers_after_transient_failures(self) -> None:
        provider = FakeProvider(retry_base_delay=0.0, max_retries=2)
        provider.fail_next(2, LLMRateLimitError())

        completion = await provider.complete(QUESTION)

        assert completion.text
        assert completion.retries == 2, "the attempt count must reach telemetry"

    async def test_gives_up_after_the_retry_budget(self) -> None:
        provider = FakeProvider(retry_base_delay=0.0, max_retries=1)
        provider.fail_next(5, LLMRateLimitError())

        with pytest.raises(LLMRateLimitError):
            await provider.complete(QUESTION)

    async def test_does_not_retry_a_permanent_failure(self) -> None:
        provider = FakeProvider(retry_base_delay=0.0, max_retries=3)
        provider.fail_next(1, LLMBadRequestError())

        with pytest.raises(LLMBadRequestError):
            await provider.complete(QUESTION)

        # One attempt only: retrying a rejected request just adds latency.
        assert len(provider.calls) == 1

    async def test_records_usage_and_latency(self) -> None:
        completion = await FakeProvider().complete(QUESTION)

        assert completion.usage.input_tokens > 0
        assert completion.usage.output_tokens > 0
        assert completion.usage.total_tokens == (
            completion.usage.input_tokens + completion.usage.output_tokens
        )
        assert completion.latency_ms >= 0


class TestStreamContract:
    async def test_yields_deltas_then_exactly_one_finished(self) -> None:
        events = [event async for event in FakeProvider().stream(QUESTION)]

        assert len(events) > 1
        assert all(isinstance(event, TextDelta) for event in events[:-1])
        assert isinstance(events[-1], StreamFinished)

    async def test_concatenated_deltas_equal_the_final_text(self) -> None:
        text, finished = await collect(FakeProvider())

        assert isinstance(finished, StreamFinished)
        assert text == finished.completion.text

    async def test_retries_when_it_fails_before_emitting_anything(self) -> None:
        provider = FakeProvider(retry_base_delay=0.0, max_retries=2)
        provider.fail_next(1, LLMRateLimitError())

        text, finished = await collect(provider)

        assert text
        assert isinstance(finished, StreamFinished)

    async def test_surfaces_a_failure_that_happens_mid_stream(self) -> None:
        """Replaying a partly-delivered stream would duplicate text on the client."""

        class FailsMidStream(FakeProvider):
            async def _stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
                yield TextDelta(text="partial ")
                raise LLMRateLimitError()

        provider = FailsMidStream(retry_base_delay=0.0, max_retries=3)

        received: list[str] = []
        with pytest.raises(LLMRateLimitError):
            async for event in provider.stream(QUESTION):
                assert isinstance(event, TextDelta)
                received.append(event.text)

        assert received == ["partial "], "the caller keeps what already arrived"


class TestFakeProvider:
    async def test_is_deterministic_for_the_same_input(self) -> None:
        first = await FakeProvider().complete(QUESTION)
        second = await FakeProvider().complete(QUESTION)
        assert first.text == second.text

    async def test_differs_for_different_input(self) -> None:
        first = await FakeProvider().complete([Message(role=Role.USER, content="a")])
        second = await FakeProvider().complete([Message(role=Role.USER, content="b")])
        assert first.text != second.text

    async def test_labels_itself_so_output_is_never_mistaken_for_a_model(self) -> None:
        completion = await FakeProvider().complete(QUESTION)
        assert "test provider" in completion.text.lower()

    async def test_scripted_responses_are_returned_in_order(self) -> None:
        provider = FakeProvider()
        provider.script("first answer", "second answer")

        assert (await provider.complete(QUESTION)).text == "first answer"
        assert (await provider.complete(QUESTION)).text == "second answer"

    async def test_needs_no_credentials(self) -> None:
        FakeProvider().validate_configuration()  # must not raise


class TestConfigurationValidation:
    async def test_a_provider_without_its_key_fails_fast(self) -> None:
        from app.llm.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key=None)
        with pytest.raises(LLMConfigurationError):
            provider.validate_configuration()
        await provider.aclose()

    async def test_a_provider_with_a_key_validates(self) -> None:
        from app.llm.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="sk-ant-not-a-real-key")
        provider.validate_configuration()  # must not raise
        await provider.aclose()
