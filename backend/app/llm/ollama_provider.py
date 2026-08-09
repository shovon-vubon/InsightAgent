"""Ollama provider for local open-source models.

Talks to the HTTP API with httpx rather than pulling in the `ollama` client, which
would add a dependency for two endpoints. No API key: the trade is that it runs on
the developer's GPU.

On a 4 GB card this realistically means a 3B-class model. Its tool-calling and
instruction-following are materially weaker than the hosted providers', and the
model comparison in later phases must say so rather than presenting it as a fair
open-source baseline.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar

import httpx

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
    LLMBadRequestError,
    LLMProviderError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


def _translate(exc: Exception) -> LLMProviderError:
    match exc:
        case httpx.TimeoutException():
            return LLMTimeoutError()
        case httpx.ConnectError():
            return LLMServiceUnavailableError(
                "Ollama is not reachable. Is it installed and running?"
            )
        case httpx.HTTPStatusError() as status_error:
            code = status_error.response.status_code
            if code == 404:
                return LLMBadRequestError("The requested model is not pulled in Ollama.")
            if code >= 500:
                return LLMServiceUnavailableError()
            return LLMBadRequestError(f"Ollama rejected the request ({code}).")
        case _:
            return LLMProviderError(f"Ollama request failed: {type(exc).__name__}")


class OllamaProvider(LLMProvider):
    name: ClassVar[str] = "ollama"
    requires_api_key: ClassVar[bool] = False

    DEFAULT_MODEL: ClassVar[str] = "qwen2.5:3b-instruct"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        default_model: str | None = None,
        timeout_seconds: float = 120.0,
        max_retries: int = 1,
        retry_base_delay: float = 0.5,
    ) -> None:
        # Local inference is slow; a shorter timeout would abort valid generations.
        super().__init__(
            default_model=default_model or self.DEFAULT_MODEL,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    @staticmethod
    def _payload(
        messages: Sequence[Message], model: str, temperature: float, max_tokens: int, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "stream": stream,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

    @staticmethod
    def _usage_from(body: dict[str, Any]) -> Usage:
        return Usage(
            input_tokens=int(body.get("prompt_eval_count") or 0),
            output_tokens=int(body.get("eval_count") or 0),
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
            response = await self._client.post(
                "/api/chat", json=self._payload(messages, model, temperature, max_tokens, False)
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except Exception as exc:
            raise _translate(exc) from exc

        return (
            str(body.get("message", {}).get("content", "")),
            self._usage_from(body),
            str(body.get("done_reason") or "stop"),
        )

    async def _stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        collected: list[str] = []
        usage = Usage()
        finish_reason = "stop"

        try:
            async with self._client.stream(
                "POST",
                "/api/chat",
                json=self._payload(messages, model, temperature, max_tokens, True),
            ) as response:
                response.raise_for_status()
                # NDJSON: one JSON object per line, the last carrying the counters.
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event: dict[str, Any] = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    delta = event.get("message", {}).get("content", "")
                    if delta:
                        collected.append(delta)
                        yield TextDelta(text=delta)

                    if event.get("done"):
                        usage = self._usage_from(event)
                        finish_reason = str(event.get("done_reason") or "stop")
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
        await self._client.aclose()
