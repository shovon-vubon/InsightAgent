"""Provider-agnostic LLM interface.

Nothing above this layer imports a vendor SDK. Providers are swapped through
configuration, and the rest of the system depends only on the types declared here
(brief §3: do not hard-code the system around one provider).

The base class owns timing, retry, and error classification so each provider only
implements the two thin methods that actually talk to its API.
"""

from __future__ import annotations

import abc
import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from app.core.logging import get_logger
from app.llm.errors import (
    LLMConfigurationError,
    LLMProviderError,
    is_retryable,
)

logger = get_logger(__name__)


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Prompt-cache hits are billed at a reduced rate by some providers, so they are
    # tracked separately rather than folded into input_tokens.
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    provider: str
    model: str
    usage: Usage
    finish_reason: str
    latency_ms: float
    # Number of attempts that failed before this one succeeded. Recorded rather
    # than swallowed, so retry pressure is visible in the telemetry.
    retries: int = 0


@dataclass(frozen=True, slots=True)
class TextDelta:
    """An incremental piece of the response."""

    text: str
    type: str = field(default="delta", init=False)


@dataclass(frozen=True, slots=True)
class StreamFinished:
    """Terminal event carrying the accounting the deltas cannot."""

    completion: Completion
    type: str = field(default="finished", init=False)


StreamEvent = TextDelta | StreamFinished


class LLMProvider(abc.ABC):
    """Base class for every provider.

    Subclasses implement `_complete` and `_stream` and raise the exceptions in
    `app.llm.errors`; retry, timing, and logging are handled here.
    """

    name: ClassVar[str]
    #: Whether the provider needs a credential. Local providers do not.
    requires_api_key: ClassVar[bool] = True

    def __init__(
        self,
        *,
        default_model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    # --- public API ---------------------------------------------------------

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> Completion:
        resolved_model = model or self.default_model
        started = time.perf_counter()
        last_error: LLMProviderError | None = None

        for attempt in range(self.max_retries + 1):
            try:
                text, usage, finish_reason = await asyncio.wait_for(
                    self._complete(
                        messages,
                        model=resolved_model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                last_error = LLMProviderError(
                    f"{self.name} timed out after {self.timeout_seconds}s"
                )
                last_error.__cause__ = exc
            except LLMProviderError as exc:
                last_error = exc
            else:
                return Completion(
                    text=text,
                    provider=self.name,
                    model=resolved_model,
                    usage=usage,
                    finish_reason=finish_reason,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    retries=attempt,
                )

            if attempt >= self.max_retries or not is_retryable(last_error):
                break
            await self._sleep_before_retry(attempt, last_error)

        assert last_error is not None  # noqa: S101 - loop always sets it before breaking
        logger.warning(
            "llm_call_failed",
            provider=self.name,
            model=resolved_model,
            error_code=last_error.error_code,
            attempts=self.max_retries + 1,
        )
        raise last_error

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> AsyncIterator[StreamEvent]:
        """Yield deltas, then exactly one `StreamFinished`.

        Retries only while nothing has been emitted yet. Once a delta has reached
        the client, replaying the call would duplicate text, so a mid-stream
        failure is surfaced rather than retried.
        """
        resolved_model = model or self.default_model

        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            emitted = False
            try:
                async for event in self._stream(
                    messages,
                    model=resolved_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if isinstance(event, TextDelta):
                        emitted = True
                        yield event
                    else:
                        yield StreamFinished(
                            completion=Completion(
                                text=event.completion.text,
                                provider=self.name,
                                model=resolved_model,
                                usage=event.completion.usage,
                                finish_reason=event.completion.finish_reason,
                                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                                retries=attempt,
                            )
                        )
                return
            except LLMProviderError as exc:
                if emitted or attempt >= self.max_retries or not is_retryable(exc):
                    logger.warning(
                        "llm_stream_failed",
                        provider=self.name,
                        model=resolved_model,
                        error_code=exc.error_code,
                        partial_output=emitted,
                    )
                    raise
                await self._sleep_before_retry(attempt, exc)

    async def _sleep_before_retry(self, attempt: int, error: LLMProviderError) -> None:
        delay = self.retry_base_delay * (2**attempt)
        logger.info(
            "llm_retry",
            provider=self.name,
            attempt=attempt + 1,
            delay_seconds=delay,
            error_code=error.error_code,
        )
        await asyncio.sleep(delay)

    def validate_configuration(self) -> None:
        """Raise if the provider cannot possibly work. Called at startup."""
        if self.requires_api_key and not self._has_credentials():
            raise LLMConfigurationError(
                f"{self.name} is selected but its API key is not configured."
            )

    # --- to implement -------------------------------------------------------

    def _has_credentials(self) -> bool:
        return True

    @abc.abstractmethod
    async def _complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, Usage, str]:
        """Return (text, usage, finish_reason)."""

    @abc.abstractmethod
    def _stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        """Yield `TextDelta`s then one `StreamFinished`."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not every provider holds connections
        """Release any held connections. Default is a no-op."""
