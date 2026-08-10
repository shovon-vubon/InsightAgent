"""Content-addressed cache in front of an embedding provider.

Re-ingesting an unchanged document, or asking the same question twice, then costs
nothing. That matters more than it sounds: the evaluation harness in Phase 9
re-runs the same corpus and the same question set across configurations, and
without this every run pays the full embedding bill again (risk R5).

The cache is keyed by `(model, input_type, sha256(text))`, never by chunk id — two
identical paragraphs in different documents share one entry, and editing a
document invalidates only the chunks that actually changed.

Implemented as an `EmbeddingProvider` that decorates another one, and only
`_embed` is overridden. Batching, retry, timing, and the dimension check all stay
in the base class rather than being reimplemented here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import ClassVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.rag.embeddings.base import EmbeddingProvider, InputType

logger = get_logger(__name__)

CACHE_NAMESPACE = "insightagent:embed"


def cache_key(*, model: str, input_type: InputType, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{CACHE_NAMESPACE}:{model}:{input_type.value}:{digest}"


class CachingEmbeddingProvider(EmbeddingProvider):
    """Wraps a provider with a Redis read-through cache.

    A Redis outage degrades this to the uncached provider: every failure path
    falls through to the real call rather than raising. Losing the cache must cost
    money and latency, never correctness.
    """

    name: ClassVar[str] = "cached"
    requires_api_key: ClassVar[bool] = False

    def __init__(
        self,
        inner: EmbeddingProvider,
        redis: Redis,
        *,
        ttl_seconds: int = 604_800,
    ) -> None:
        super().__init__(
            default_model=inner.default_model,
            dimensions=inner.dimensions,
            batch_size=inner.batch_size,
            timeout_seconds=inner.timeout_seconds,
            # Retry belongs to the inner provider; retrying here would square the
            # attempt count for a failure that has already exhausted its budget.
            max_retries=0,
            retry_base_delay=0.0,
        )
        self._inner = inner
        self._redis = redis
        self._ttl = ttl_seconds
        #: Test-visible counters. Phase 12 reports the hit ratio from these.
        self.hits = 0
        self.misses = 0

    @property
    def provider_label(self) -> str:
        """Vectors are attributed to the provider that computed them, not the cache."""
        return self._inner.provider_label

    def validate_configuration(self) -> None:
        self._inner.validate_configuration()

    async def _load(self, keys: list[str]) -> list[list[float] | None]:
        if self._ttl <= 0:
            return [None] * len(keys)
        try:
            raw_values = await self._redis.mget(keys)
        except RedisError:
            logger.warning("embedding_cache_unavailable", operation="mget")
            return [None] * len(keys)

        loaded: list[list[float] | None] = []
        for raw in raw_values:
            if raw is None:
                loaded.append(None)
                continue
            try:
                vector = json.loads(raw)
            except json.JSONDecodeError:
                loaded.append(None)
                continue
            # A stored vector of the wrong size means the model was reconfigured
            # under a name that was reused. Treat it as a miss rather than
            # poisoning the corpus with a mismatched dimension.
            if isinstance(vector, list) and len(vector) == self.dimensions:
                loaded.append([float(value) for value in vector])
            else:
                loaded.append(None)
        return loaded

    async def _store(self, entries: dict[str, list[float]]) -> None:
        if self._ttl <= 0 or not entries:
            return
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for key, vector in entries.items():
                    pipe.set(key, json.dumps(vector), ex=self._ttl)
                await pipe.execute()
        except RedisError:
            logger.warning("embedding_cache_unavailable", operation="set")

    async def _embed(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int]:
        keys = [cache_key(model=model, input_type=input_type, text=text) for text in texts]
        cached = await self._load(keys)

        # Deduplicate within the batch too: a repeated string should be embedded
        # once even on a cold cache.
        pending: dict[str, int] = {}
        for index, (text, hit) in enumerate(zip(texts, cached, strict=True)):
            if hit is None and text not in pending:
                pending[text] = index

        self.hits += sum(1 for hit in cached if hit is not None)
        self.misses += len(texts) - sum(1 for hit in cached if hit is not None)

        fresh: dict[str, list[float]] = {}
        input_tokens = 0
        if pending:
            result = await self._inner.embed(list(pending), input_type=input_type, model=model)
            input_tokens = result.input_tokens
            fresh = dict(zip(pending, result.vectors, strict=True))
            await self._store(
                {
                    cache_key(model=model, input_type=input_type, text=text): vector
                    for text, vector in fresh.items()
                }
            )

        vectors: list[list[float]] = []
        for text, hit in zip(texts, cached, strict=True):
            vectors.append(hit if hit is not None else fresh[text])
        return vectors, input_tokens

    async def aclose(self) -> None:
        await self._inner.aclose()
