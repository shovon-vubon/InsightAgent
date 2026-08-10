"""Deterministic embedding provider for tests and an unconfigured install.

This is a **hashing bag-of-words vectoriser**, not a random hash. Each token is
hashed into a bucket, counts are sublinearly scaled, and the vector is L2
normalised — so two texts that share vocabulary genuinely land near each other in
cosine space.

That property is the whole point. A random-hash test double would make every
retrieval test a tautology: the pipeline would "work" while ranking arbitrary
chunks first, and a real ordering bug would pass. With this one, a test can assert
that a question about revenue retrieves the revenue chunk, and the assertion means
something.

**It is lexical, not semantic.** It has no synonymy, no word order, no
cross-lingual behaviour. It proves the plumbing — never retrieval *quality*. No
number produced against it may be reported as a retrieval metric, and
`EMBEDDING_PROVIDER=fake` is refused in production.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import ClassVar

from app.rag.embeddings.base import EmbeddingProvider, InputType

_TOKEN = re.compile(r"[a-z0-9]+")

#: Small enough to stay fast in tests, large enough that collisions between the
#: few hundred distinct tokens in a test corpus stay rare.
DEFAULT_DIMENSIONS = 256


def _tokenise(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _bucket(token: str, dimensions: int) -> int:
    # blake2b with a fixed digest size: stable across processes and Python's hash
    # randomisation, which `hash()` is not.
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


class FakeEmbeddingProvider(EmbeddingProvider):
    name: ClassVar[str] = "fake"
    requires_api_key: ClassVar[bool] = False

    DEFAULT_MODEL: ClassVar[str] = "fake-embed-1"

    def __init__(
        self,
        *,
        default_model: str | None = None,
        dimensions: int = DEFAULT_DIMENSIONS,
        batch_size: int = 32,
        timeout_seconds: float = 10.0,
        max_retries: int = 0,
        retry_base_delay: float = 0.0,
    ) -> None:
        super().__init__(
            default_model=default_model or self.DEFAULT_MODEL,
            dimensions=dimensions,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        #: Test-visible record of what was embedded, and how.
        self.calls: list[tuple[list[str], InputType]] = []

    def vector_for(self, text: str) -> list[float]:
        """The embedding of one string. Exposed so tests can build expectations."""
        counts = Counter(_tokenise(text))
        vector = [0.0] * self.dimensions
        for token, count in counts.items():
            # Sublinear scaling, as TF-IDF does: a word appearing ten times is not
            # ten times as characteristic of the text.
            vector[_bucket(token, self.dimensions)] += 1.0 + math.log(count)

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # An empty or punctuation-only string. A zero vector has undefined
            # cosine similarity, so return a fixed unit vector instead — pgvector
            # would otherwise emit NaN distances that sort unpredictably.
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    async def _embed(
        self, texts: Sequence[str], *, model: str, input_type: InputType
    ) -> tuple[list[list[float]], int]:
        self.calls.append((list(texts), input_type))
        vectors = [self.vector_for(text) for text in texts]
        return vectors, sum(len(_tokenise(text)) for text in texts)
