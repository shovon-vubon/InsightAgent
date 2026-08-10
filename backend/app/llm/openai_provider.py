"""OpenAI provider.

Uses the Chat Completions API rather than the newer Responses API: it is the
stable, widely-documented surface, and nothing here needs Responses-only features
(brief §65 — prefer stable APIs).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import ClassVar, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.completion_usage import CompletionUsage

from app.llm.base import (
    Completion,
    LLMProvider,
    Message,
    StreamEvent,
    StreamFinished,
    TextDelta,
    Usage,
)
from app.llm.errors import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMContentFilterError,
    LLMProviderError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


def _translate(exc: Exception) -> LLMProviderError:
    """Map the SDK's exception hierarchy onto ours."""
    match exc:
        case openai.RateLimitError():
            return LLMRateLimitError()
        case openai.APITimeoutError():
            return LLMTimeoutError()
        case openai.AuthenticationError() | openai.PermissionDeniedError():
            return LLMAuthenticationError()
        case openai.BadRequestError():
            return LLMBadRequestError(str(exc)[:200])
        case openai.InternalServerError() | openai.APIConnectionError():
            return LLMServiceUnavailableError()
        case _:
            return LLMProviderError(f"OpenAI request failed: {type(exc).__name__}")


class OpenAIProvider(LLMProvider):
    name: ClassVar[str] = "openai"
    DEFAULT_MODEL: ClassVar[str] = "gpt-4o-mini"

    def __init__(
        self,
        *,
        api_key: str | None,
        default_model: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        super().__init__(
            default_model=default_model or self.DEFAULT_MODEL,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        self._api_key = api_key
        # max_retries=0: retry is owned by the base class so that every provider
        # retries identically and the attempt count reaches our telemetry.
        self._client = AsyncOpenAI(
            api_key=api_key or "missing", timeout=timeout_seconds, max_retries=0
        )

    def _has_credentials(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def _to_payload(messages: Sequence[Message]) -> list[ChatCompletionMessageParam]:
        return cast(
            "list[ChatCompletionMessageParam]",
            [{"role": message.role.value, "content": message.content} for message in messages],
        )

    @staticmethod
    def _usage_from(raw: CompletionUsage | None) -> Usage:
        if raw is None:
            return Usage()
        details = raw.prompt_tokens_details
        return Usage(
            input_tokens=raw.prompt_tokens,
            output_tokens=raw.completion_tokens,
            cached_input_tokens=(details.cached_tokens or 0) if details is not None else 0,
        )

    async def _complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, Usage, str]:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=self._to_payload(messages),
                temperature=temperature,
                max_completion_tokens=max_tokens,
            )
        except Exception as exc:
            raise _translate(exc) from exc

        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise LLMContentFilterError()

        # `finish_reason` is non-optional on a non-streamed completion.
        return choice.message.content or "", self._usage_from(response.usage), choice.finish_reason

    async def _stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=self._to_payload(messages),
                temperature=temperature,
                max_completion_tokens=max_tokens,
                stream=True,
                # Without this the final chunk carries no usage and cost tracking
                # would silently record zeros.
                stream_options={"include_usage": True},
            )
        except Exception as exc:
            raise _translate(exc) from exc

        collected: list[str] = []
        usage = Usage()
        finish_reason = "stop"

        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = self._usage_from(chunk.usage)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta.content
                if delta:
                    collected.append(delta)
                    yield TextDelta(text=delta)
        except Exception as exc:
            raise _translate(exc) from exc

        yield StreamFinished(
            completion=Completion(
                text="".join(collected),
                provider=self.name,
                model=model,
                usage=usage,
                finish_reason=finish_reason,
                latency_ms=0.0,
            )
        )

    async def aclose(self) -> None:
        await self._client.close()
