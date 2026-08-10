"""Ollama embeddings — the default provider.

Chosen for Phase 3 because it needs no API key and no `torch` in the backend
image: the model runs in the Ollama process and is reached over HTTP. That keeps
risk R1 (disk pressure on `C:`) deferred rather than triggered, and makes the
knowledge base free to run and re-run during development, which matters when the
evaluation harness in Phase 9 re-embeds the corpus across configurations.

`nomic-embed-text` is **asymmetric**: it is trained with `search_document:` and
`search_query:` prefixes, and omitting them costs real recall. The prefixes are
applied here rather than by callers, because a caller that forgets produces a
corpus that looks fine and retrieves badly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

import httpx

from app.llm.errors import (
    LLMBadRequestError,
    LLMProviderError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from app.rag.embeddings.base import EmbeddingProvider, InputType

#: Prefix per input type, per model family. Models absent from this table are
#: symmetric and get no prefix.
TASK_PREFIXES: dict[str, dict[InputType, str]] = {
    "nomic-embed-text": {
        InputType.DOCUMENT: "search_document: ",
        InputType.QUERY: "search_query: ",
    },
}

#: Output dimensions per model. Required up front because the database needs to
#: know the dimension to build the partial index, before any vector exists.
MODEL_DIMENSIONS: dict[str, int] = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "bge-m3": 1024,
    "snowflake-arctic-embed": 1024,
}


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
                return LLMBadRequestError(
                    "The embedding model is not pulled in Ollama. Run: ollama pull nomic-embed-text"
                )
            if code >= 500:
                return LLMServiceUnavailableError()
            return LLMBadRequestError(f"Ollama rejected the embedding request ({code}).")
        case _:
            return LLMProviderError(f"Ollama embedding request failed: {type(exc).__name__}")


def _base_model_name(model: str) -> str:
    """Strip an Ollama tag: `nomic-embed-text:v1.5` -> `nomic-embed-text`."""
    return model.split(":", 1)[0]


class OllamaEmbeddingProvider(EmbeddingProvider):
    name: ClassVar[str] = "ollama"
    requires_api_key: ClassVar[bool] = False

    DEFAULT_MODEL: ClassVar[str] = "nomic-embed-text"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        default_model: str | None = None,
        dimensions: int | None = None,
        batch_size: int = 32,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        model = default_model or self.DEFAULT_MODEL
        resolved_dimensions = dimensions or MODEL_DIMENSIONS.get(_base_model_name(model))
        if resolved_dimensions is None:
            raise LLMBadRequestError(
                f"Unknown embedding dimension for Ollama model '{model}'. "
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
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    @staticmethod
    def _apply_prefix(texts: Sequence[str], model: str, input_type: InputType) -> list[str]:
        prefixes = TASK_PREFIXES.get(_base_model_name(model))
        if prefixes is None:
            return list(texts)
        prefix = prefixes[input_type]
        return [prefix + text for text in texts]

    async def _embed(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int]:
        try:
            # /api/embed (not the deprecated /api/embeddings) takes a list and
            # returns them in order.
            response = await self._client.post(
                "/api/embed",
                json={"model": model, "input": self._apply_prefix(texts, model, input_type)},
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except Exception as exc:
            raise _translate(exc) from exc

        raw = body.get("embeddings")
        if not isinstance(raw, list):
            raise LLMProviderError("Ollama returned no embeddings.")

        vectors = [[float(value) for value in vector] for vector in raw]
        return vectors, int(body.get("prompt_eval_count") or 0)

    async def aclose(self) -> None:
        await self._client.aclose()
