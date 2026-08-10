"""Embedding provider behaviour.

The most important test here is `test_shared_vocabulary_scores_higher`. The fake
provider is what every retrieval test in the suite runs against, so if it did not
put lexically similar texts near each other, those tests would pass while ranking
arbitrary chunks first — proving nothing. That property is asserted rather than
assumed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from app.llm.errors import LLMProviderError, LLMRateLimitError
from app.rag.embeddings.base import EmbeddingProvider, InputType
from app.rag.embeddings.fake import FakeEmbeddingProvider
from app.rag.embeddings.ollama import MODEL_DIMENSIONS, OllamaEmbeddingProvider


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class TestFakeEmbeddings:
    async def test_vectors_are_unit_length(self) -> None:
        provider = FakeEmbeddingProvider()
        result = await provider.embed(["some text about revenue"])
        norm = math.sqrt(sum(value**2 for value in result.vectors[0]))
        assert norm == pytest.approx(1.0)

    async def test_deterministic_across_instances(self) -> None:
        first = await FakeEmbeddingProvider().embed(["identical text"])
        second = await FakeEmbeddingProvider().embed(["identical text"])
        assert first.vectors == second.vectors

    async def test_shared_vocabulary_scores_higher(self) -> None:
        """The property that makes retrieval tests meaningful."""
        provider = FakeEmbeddingProvider()
        query = provider.vector_for("what was quarterly revenue in EMEA")
        relevant = provider.vector_for("EMEA quarterly revenue was 1.1 million")
        irrelevant = provider.vector_for("the cafeteria menu changes on Fridays")

        assert cosine(query, relevant) > cosine(query, irrelevant)
        assert cosine(query, relevant) > 0.3

    async def test_empty_string_does_not_produce_a_zero_vector(self) -> None:
        # A zero vector has undefined cosine distance and would sort
        # unpredictably in pgvector.
        vector = FakeEmbeddingProvider().vector_for("")
        assert math.sqrt(sum(v**2 for v in vector)) == pytest.approx(1.0)

    async def test_batch_order_is_preserved(self) -> None:
        provider = FakeEmbeddingProvider(batch_size=2)
        texts = ["alpha", "beta", "gamma", "delta", "epsilon"]
        result = await provider.embed(texts)

        assert len(result.vectors) == 5
        for text, vector in zip(texts, result.vectors, strict=True):
            assert vector == provider.vector_for(text)

    async def test_empty_input_returns_empty_result(self) -> None:
        result = await FakeEmbeddingProvider().embed([])
        assert result.vectors == []
        assert result.dimensions == 256

    async def test_query_and_document_input_types_are_recorded(self) -> None:
        provider = FakeEmbeddingProvider()
        await provider.embed(["a"], input_type=InputType.DOCUMENT)
        await provider.embed_query("a")
        assert [call[1] for call in provider.calls] == [InputType.DOCUMENT, InputType.QUERY]


class _BrokenProvider(EmbeddingProvider):
    """Returns the wrong number of vectors — the corpus-corrupting failure."""

    name = "broken"
    requires_api_key = False

    async def _embed(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int]:
        return [[0.0] * self.dimensions], 0


class _WrongWidthProvider(EmbeddingProvider):
    name = "wrong-width"
    requires_api_key = False

    async def _embed(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int]:
        return [[0.0] * 99 for _ in texts], 0


class _FlakyProvider(EmbeddingProvider):
    name = "flaky"
    requires_api_key = False

    def __init__(self, failures: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.remaining = failures
        self.attempts = 0

    async def _embed(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int]:
        self.attempts += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise LLMRateLimitError()
        return [[1.0] + [0.0] * (self.dimensions - 1) for _ in texts], 0


class TestBaseGuarantees:
    async def test_vector_count_mismatch_is_fatal(self) -> None:
        # Silently accepting this would misalign every vector with its chunk,
        # which looks like poor retrieval rather than a bug.
        provider = _BrokenProvider(default_model="m", dimensions=4)
        with pytest.raises(LLMProviderError, match="returned 1 vectors for 3"):
            await provider.embed(["a", "b", "c"])

    async def test_dimension_mismatch_is_fatal(self) -> None:
        provider = _WrongWidthProvider(default_model="m", dimensions=4)
        with pytest.raises(LLMProviderError, match="99-dimension"):
            await provider.embed(["a"])

    async def test_retryable_failures_are_retried(self) -> None:
        provider = _FlakyProvider(
            2, default_model="m", dimensions=4, max_retries=2, retry_base_delay=0.0
        )
        result = await provider.embed(["a"])
        assert provider.attempts == 3
        assert result.retries == 2

    async def test_retries_are_bounded(self) -> None:
        provider = _FlakyProvider(
            5, default_model="m", dimensions=4, max_retries=1, retry_base_delay=0.0
        )
        with pytest.raises(LLMProviderError):
            await provider.embed(["a"])
        assert provider.attempts == 2

    async def test_batching_splits_large_inputs(self) -> None:
        provider = _FlakyProvider(
            0, default_model="m", dimensions=4, batch_size=2, retry_base_delay=0.0
        )
        await provider.embed(["a", "b", "c", "d", "e"])
        assert provider.attempts == 3  # 2 + 2 + 1


class TestOllamaProvider:
    def test_dimensions_resolved_from_the_model(self) -> None:
        provider = OllamaEmbeddingProvider(default_model="nomic-embed-text")
        assert provider.dimensions == MODEL_DIMENSIONS["nomic-embed-text"] == 768

    def test_tagged_model_names_resolve(self) -> None:
        provider = OllamaEmbeddingProvider(default_model="nomic-embed-text:v1.5")
        assert provider.dimensions == 768

    def test_unknown_model_fails_fast(self) -> None:
        # Better than defaulting to a guess and writing vectors of the wrong
        # width into a partial index built for another dimension.
        with pytest.raises(LLMProviderError, match="Unknown embedding dimension"):
            OllamaEmbeddingProvider(default_model="some-unlisted-model")

    def test_asymmetric_prefixes_are_applied(self) -> None:
        prefixed = OllamaEmbeddingProvider._apply_prefix(
            ["revenue"], "nomic-embed-text", InputType.QUERY
        )
        assert prefixed == ["search_query: revenue"]

        prefixed = OllamaEmbeddingProvider._apply_prefix(
            ["revenue"], "nomic-embed-text", InputType.DOCUMENT
        )
        assert prefixed == ["search_document: revenue"]

    def test_symmetric_models_get_no_prefix(self) -> None:
        assert OllamaEmbeddingProvider._apply_prefix(["x"], "all-minilm", InputType.QUERY) == ["x"]

    def test_requires_no_api_key(self) -> None:
        OllamaEmbeddingProvider(default_model="nomic-embed-text").validate_configuration()
