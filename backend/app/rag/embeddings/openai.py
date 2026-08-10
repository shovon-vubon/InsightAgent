"""OpenAI embeddings.

Implemented alongside the Ollama default so the embedding model stays a real
experiment variable (plan D3) rather than a single hard-coded choice — Phase 9
compares them, and that comparison needs both to exist.

`text-embedding-3-*` are symmetric: they take no task prefix, so `InputType` is
accepted and ignored here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

import openai
from openai import AsyncOpenAI

from app.llm.errors import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMProviderError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from app.rag.embeddings.base import EmbeddingProvider, InputType

#: Native output dimensions. The `3-*` models also support Matryoshka truncation
#: via `dimensions=`, which is why the value is passed explicitly on every request
#: rather than left implicit.
MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def _translate(exc: Exception) -> LLMProviderError:
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
            return LLMProviderError(f"OpenAI embedding request failed: {type(exc).__name__}")


class OpenAIEmbeddingProvider(EmbeddingProvider):
    name: ClassVar[str] = "openai"

    DEFAULT_MODEL: ClassVar[str] = "text-embedding-3-small"

    def __init__(
        self,
        *,
        api_key: str | None,
        default_model: str | None = None,
        dimensions: int | None = None,
        batch_size: int = 128,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        model = default_model or self.DEFAULT_MODEL
        resolved_dimensions = dimensions or MODEL_DIMENSIONS.get(model)
        if resolved_dimensions is None:
            raise LLMBadRequestError(
                f"Unknown embedding dimension for OpenAI model '{model}'. "
                f"Add it to MODEL_DIMENSIONS or set the dimension explicitly."
            )
        super().__init__(
            default_model=model,
            dimensions=resolved_dimensions,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        self._api_key = api_key
        # Retries are disabled in the SDK: the base class owns them, and two
        # layers of backoff would multiply rather than add.
        self._client = AsyncOpenAI(
            api_key=api_key or "missing", timeout=timeout_seconds, max_retries=0
        )

    def _has_credentials(self) -> bool:
        return bool(self._api_key)

    async def _embed(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int]:
        del input_type  # symmetric model: documents and queries embed identically
        try:
            # Only the `3-*` family accepts `dimensions`; ada-002 rejects the
            # parameter outright, so the call is branched rather than built from
            # **kwargs — which would also defeat the SDK's overload typing.
            if model.startswith("text-embedding-3"):
                response = await self._client.embeddings.create(
                    model=model, input=list(texts), dimensions=self.dimensions
                )
            else:
                response = await self._client.embeddings.create(model=model, input=list(texts))
        except Exception as exc:
            raise _translate(exc) from exc

        # The API documents index ordering but does not guarantee it in the
        # response body; sorting makes the alignment ours rather than assumed.
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered], response.usage.prompt_tokens

    async def aclose(self) -> None:
        await self._client.close()
