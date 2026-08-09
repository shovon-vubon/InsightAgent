"""Deterministic provider used by tests and by an unconfigured local install.

Every test that exercises agent logic, streaming, cost recording, or error
handling runs against this. That keeps CI free, fast, and repeatable — a suite
that calls a real model tests the model, not the code (brief §43).

It is also the default in `.env.example` so a fresh clone runs end to end before
any API key exists. The UI labels it, so nobody mistakes it for a real answer.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Sequence
from typing import ClassVar

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
from app.llm.errors import LLMProviderError

FAKE_NOTICE = (
    "[deterministic test provider — no language model was called; "
    "configure LLM_PROVIDER with an API key for real responses]"
)


def _approximate_tokens(text: str) -> int:
    """Whitespace word count.

    Deliberately not tiktoken: this provider must stay dependency-free and its
    numbers are never used for billing, only to prove the accounting path works.
    """
    return len(text.split())


class FakeProvider(LLMProvider):
    name: ClassVar[str] = "fake"
    requires_api_key: ClassVar[bool] = False

    DEFAULT_MODEL: ClassVar[str] = "fake-1"

    def __init__(
        self,
        *,
        default_model: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.0,
        scripted_responses: Sequence[str] | None = None,
        fail_times: int = 0,
        failure: LLMProviderError | None = None,
        chunk_size: int = 8,
    ) -> None:
        super().__init__(
            default_model=default_model or self.DEFAULT_MODEL,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        self._scripted = list(scripted_responses or [])
        self._script_index = 0
        self._fail_times = fail_times
        self._failure = failure or LLMProviderError("simulated provider failure")
        self._chunk_size = chunk_size
        #: Test-visible record of what the provider was asked.
        self.calls: list[list[Message]] = []

    # --- test controls ------------------------------------------------------

    def script(self, *responses: str) -> None:
        """Queue exact responses. The last one repeats once the queue is spent."""
        self._scripted = list(responses)
        self._script_index = 0

    def fail_next(self, times: int, error: LLMProviderError | None = None) -> None:
        """Make the next `times` calls raise, then behave normally."""
        self._fail_times = times
        if error is not None:
            self._failure = error

    # --- generation ---------------------------------------------------------

    def _next_text(self, messages: Sequence[Message]) -> str:
        if self._scripted:
            text = self._scripted[min(self._script_index, len(self._scripted) - 1)]
            self._script_index += 1
            return text

        last_user = next(
            (m.content for m in reversed(messages) if m.role is Role.USER),
            "",
        )
        # Hash makes the reply stable for a given input without being a bare echo,
        # so tests can assert determinism.
        digest = hashlib.sha256(last_user.encode("utf-8")).hexdigest()[:8]
        return f"{FAKE_NOTICE} Received {_approximate_tokens(last_user)} tokens (ref {digest})."

    def _maybe_fail(self) -> None:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise self._failure

    def _usage(self, messages: Sequence[Message], text: str) -> Usage:
        return Usage(
            input_tokens=sum(_approximate_tokens(m.content) for m in messages),
            output_tokens=_approximate_tokens(text),
        )

    async def _complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, Usage, str]:
        self.calls.append(list(messages))
        self._maybe_fail()
        text = self._next_text(messages)
        return text, self._usage(messages, text), "stop"

    async def _stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        self._maybe_fail()

        text = self._next_text(messages)
        for start in range(0, len(text), self._chunk_size):
            yield TextDelta(text=text[start : start + self._chunk_size])

        yield StreamFinished(
            completion=Completion(
                text=text,
                provider=self.name,
                model=model,
                usage=self._usage(messages, text),
                finish_reason="stop",
                latency_ms=0.0,
            )
        )
