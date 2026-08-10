"""Provider-agnostic embedding interface.

Mirrors `app.llm.base` deliberately: the base class owns batching, retry, timing,
and error classification, and each provider implements one thin method that talks
to its API. Nothing above this layer imports a vendor SDK.

Two details here are easy to get wrong and expensive to discover later.

**Document and query embeddings are not the same call.** Several modern embedding
models are *asymmetric* — they are trained with a task prefix, and embedding a
question the way you embed a passage measurably degrades recall. `InputType` makes
the distinction explicit at the call site instead of leaving it to whoever
remembers.

**Errors reuse `app.llm.errors`.** An embedding endpoint fails in exactly the ways
a completion endpoint does (rate limit, timeout, 5xx, bad credential), and the
retry classifier is already written against that taxonomy. A parallel hierarchy
would be duplication with no new information in it.
"""

from __future__ import annotations

import abc
import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from app.core.logging import get_logger
from app.llm.errors import (
    LLMConfigurationError,
    LLMProviderError,
    is_retryable,
)

logger = get_logger(__name__)


class InputType(StrEnum):
    """What the text is for. Asymmetric models prefix the two differently."""

    DOCUMENT = "document"
    QUERY = "query"


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str
    dimensions: int
    #: Zero where the provider does not report it (Ollama does not).
    input_tokens: int = 0
    latency_ms: float = 0.0
    retries: int = 0


class EmbeddingProvider(abc.ABC):
    """Base class for every embedding provider.

    Subclasses implement `_embed` and raise from `app.llm.errors`; batching,
    retry, and timing are handled here.
    """

    name: ClassVar[str]
    requires_api_key: ClassVar[bool] = True

    @property
    def provider_label(self) -> str:
        """Who actually computed the vectors.

        Normally just `name`. Decorators such as the cache override it so that
        telemetry attributes vectors to the provider that produced them rather
        than to the wrapper they passed through.
        """
        return self.name

    def __init__(
        self,
        *,
        default_model: str,
        dimensions: int,
        batch_size: int = 32,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        self.default_model = default_model
        #: Declared, not measured. `verify_dimensions` checks the claim at startup.
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    # --- public API ---------------------------------------------------------

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: InputType = InputType.DOCUMENT,
        model: str | None = None,
    ) -> EmbeddingResult:
        """Embed a batch, splitting it into provider-sized requests."""
        resolved_model = model or self.default_model
        started = time.perf_counter()

        if not texts:
            return EmbeddingResult(
                vectors=[],
                provider=self.provider_label,
                model=resolved_model,
                dimensions=self.dimensions,
            )

        vectors: list[list[float]] = []
        input_tokens = 0
        total_retries = 0

        for start in range(0, len(texts), self.batch_size):
            window = texts[start : start + self.batch_size]
            batch_vectors, batch_tokens, retries = await self._embed_with_retry(
                window, model=resolved_model, input_type=input_type
            )
            if len(batch_vectors) != len(window):
                # A provider returning the wrong count would silently misalign
                # every vector with its chunk — corrupting the corpus in a way
                # that looks like poor retrieval quality rather than a bug.
                raise LLMProviderError(
                    f"{self.name} returned {len(batch_vectors)} vectors for {len(window)} inputs."
                )
            vectors.extend(batch_vectors)
            input_tokens += batch_tokens
            total_retries += retries

        self._assert_dimensions(vectors, resolved_model)

        return EmbeddingResult(
            vectors=vectors,
            provider=self.provider_label,
            model=resolved_model,
            dimensions=self.dimensions,
            input_tokens=input_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            retries=total_retries,
        )

    async def embed_query(self, text: str, *, model: str | None = None) -> list[float]:
        result = await self.embed([text], input_type=InputType.QUERY, model=model)
        return result.vectors[0]

    def _assert_dimensions(self, vectors: list[list[float]], model: str) -> None:
        """Fail loudly on a dimension mismatch.

        Writing a differently sized vector under the same `embedding_model` label
        would break the partial HNSW index built for that model, and the symptom
        would be a query-time cast error far from the cause.
        """
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise LLMProviderError(
                    f"{self.name}/{model} returned {len(vector)}-dimension vectors, "
                    f"but is configured as {self.dimensions}."
                )

    async def _embed_with_retry(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int, int]:
        last_error: LLMProviderError | None = None

        for attempt in range(self.max_retries + 1):
            try:
                vectors, tokens = await asyncio.wait_for(
                    self._embed(texts, model=model, input_type=input_type),
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
                return vectors, tokens, attempt

            if attempt >= self.max_retries or not is_retryable(last_error):
                break
            delay = self.retry_base_delay * (2**attempt)
            logger.info(
                "embedding_retry",
                provider=self.name,
                attempt=attempt + 1,
                delay_seconds=delay,
                error_code=last_error.error_code,
            )
            await asyncio.sleep(delay)

        assert last_error is not None  # noqa: S101 - the loop always sets it before breaking
        logger.warning(
            "embedding_call_failed",
            provider=self.name,
            model=model,
            batch_size=len(texts),
            error_code=last_error.error_code,
        )
        raise last_error

    def validate_configuration(self) -> None:
        """Raise if the provider cannot possibly work. Called at startup."""
        if self.requires_api_key and not self._has_credentials():
            raise LLMConfigurationError(
                f"{self.name} embeddings are selected but the API key is not configured."
            )

    # --- to implement -------------------------------------------------------

    def _has_credentials(self) -> bool:
        return True

    @abc.abstractmethod
    async def _embed(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int]:
        """Return (vectors, input_tokens) for one provider-sized batch."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not every provider holds connections
        """Release any held connections. Default is a no-op."""
