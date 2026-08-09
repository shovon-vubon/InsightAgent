"""Anthropic provider.

Anthropic takes the system prompt as a top-level parameter rather than as a
message, so it is split out here. That difference is exactly the kind of thing the
`LLMProvider` abstraction exists to hide.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar, cast

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from app.llm.base import (
    Completion,
    LLMProvider,
    Message,
    Role,
    StreamEvent,
    StreamFinished,
    TextDelta,
    Usage,
)
from app.llm.errors import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMProviderError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


def _translate(exc: Exception) -> LLMProviderError:
    match exc:
        case anthropic.RateLimitError():
            return LLMRateLimitError()
        case anthropic.APITimeoutError():
            return LLMTimeoutError()
        case anthropic.AuthenticationError() | anthropic.PermissionDeniedError():
            return LLMAuthenticationError()
        case anthropic.BadRequestError():
            return LLMBadRequestError(str(exc)[:200])
        case anthropic.InternalServerError() | anthropic.APIConnectionError():
            return LLMServiceUnavailableError()
        case _:
            return LLMProviderError(f"Anthropic request failed: {type(exc).__name__}")


class AnthropicProvider(LLMProvider):
    name: ClassVar[str] = "anthropic"
    DEFAULT_MODEL: ClassVar[str] = "claude-haiku-4-5-20251001"

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
        self._client = AsyncAnthropic(
            api_key=api_key or "missing", timeout=timeout_seconds, max_retries=0
        )

    def _has_credentials(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def _split(messages: Sequence[Message]) -> tuple[dict[str, Any], list[MessageParam]]:
        """Separate system content from the turn-taking messages.

        The system prompt comes back as a kwargs fragment rather than an optional
        value, because the SDK distinguishes "absent" from "None" via its own
        sentinel and building the fragment keeps that detail in one place.
        """
        system_parts = [m.content for m in messages if m.role is Role.SYSTEM]
        turns = cast(
            "list[MessageParam]",
            [
                {"role": m.role.value, "content": m.content}
                for m in messages
                if m.role is not Role.SYSTEM
            ],
        )
        system = "\n\n".join(system_parts)
        return ({"system": system} if system else {}), turns

    @staticmethod
    def _usage_from(raw: Any) -> Usage:
        if raw is None:
            return Usage()
        cached = getattr(raw, "cache_read_input_tokens", 0) or 0
        return Usage(
            # Anthropic reports cache reads outside input_tokens; folding them in
            # keeps `input_tokens` meaning "everything sent" across providers.
            input_tokens=(getattr(raw, "input_tokens", 0) or 0) + cached,
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cached_input_tokens=cached,
        )

    async def _complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, Usage, str]:
        system, turns = self._split(messages)
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=turns,
                **system,
            )
        except Exception as exc:
            raise _translate(exc) from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        return text, self._usage_from(response.usage), response.stop_reason or "end_turn"

    async def _stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        system, turns = self._split(messages)
        collected: list[str] = []

        try:
            async with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=turns,
                **system,
            ) as stream:
                async for delta in stream.text_stream:
                    collected.append(delta)
                    yield TextDelta(text=delta)

                final = await stream.get_final_message()
        except Exception as exc:
            raise _translate(exc) from exc

        yield StreamFinished(
            completion=Completion(
                text="".join(collected),
                provider=self.name,
                model=model,
                usage=self._usage_from(final.usage),
                finish_reason=final.stop_reason or "end_turn",
                latency_ms=0.0,
            )
        )

    async def aclose(self) -> None:
        await self._client.close()
