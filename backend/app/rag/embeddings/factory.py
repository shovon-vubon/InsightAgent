"""Embedding provider selection from configuration.

The only place that knows which concrete embedding providers exist. Mirrors
`app.llm.factory`; kept separate because the model that answers and the model that
embeds are configured independently (see `EMBEDDING_PROVIDER`).
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.errors import LLMConfigurationError
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.fake import FakeEmbeddingProvider
from app.rag.embeddings.ollama import OllamaEmbeddingProvider
from app.rag.embeddings.openai import OpenAIEmbeddingProvider

logger = get_logger(__name__)


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Build the configured provider, failing fast if it cannot work."""
    model = settings.EMBEDDING_MODEL
    batch_size = settings.EMBEDDING_BATCH_SIZE
    timeout = settings.EMBEDDING_TIMEOUT_SECONDS

    provider: EmbeddingProvider
    match settings.EMBEDDING_PROVIDER:
        case "ollama":
            provider = OllamaEmbeddingProvider(
                base_url=settings.OLLAMA_BASE_URL,
                default_model=model,
                batch_size=batch_size,
                timeout_seconds=timeout,
            )
        case "openai":
            provider = OpenAIEmbeddingProvider(
                api_key=settings.openai_api_key,
                default_model=model,
                batch_size=batch_size,
                timeout_seconds=timeout,
            )
        case "fake":
            provider = FakeEmbeddingProvider(default_model=model, batch_size=batch_size)
        case unknown:  # pragma: no cover - the Literal makes this unreachable
            raise LLMConfigurationError(f"Unknown embedding provider: {unknown}")

    provider.validate_configuration()
    logger.info(
        "embedding_provider_configured",
        provider=provider.name,
        model=provider.default_model,
        dimensions=provider.dimensions,
        is_test_double=provider.name == "fake",
    )
    return provider
