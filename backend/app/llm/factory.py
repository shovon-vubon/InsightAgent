"""Provider selection from configuration.

The only place that knows which concrete providers exist. Everything else depends
on `LLMProvider`.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.errors import LLMConfigurationError
from app.llm.fake import FakeProvider
from app.llm.ollama_provider import OllamaProvider
from app.llm.openai_provider import OpenAIProvider

logger = get_logger(__name__)


def create_provider(settings: Settings) -> LLMProvider:
    """Build the configured provider, failing fast if it cannot work."""
    # `LLM_MODEL` may be blank, in which case each provider falls back to its own
    # DEFAULT_MODEL. Passed explicitly rather than unpacked so the types stay checkable.
    model = settings.LLM_MODEL
    timeout = settings.LLM_TIMEOUT_SECONDS
    retries = settings.LLM_MAX_RETRIES

    provider: LLMProvider
    match settings.LLM_PROVIDER:
        case "openai":
            provider = OpenAIProvider(
                api_key=settings.openai_api_key,
                default_model=model,
                timeout_seconds=timeout,
                max_retries=retries,
            )
        case "anthropic":
            provider = AnthropicProvider(
                api_key=settings.anthropic_api_key,
                default_model=model,
                timeout_seconds=timeout,
                max_retries=retries,
            )
        case "ollama":
            provider = OllamaProvider(
                base_url=settings.OLLAMA_BASE_URL,
                default_model=model,
                timeout_seconds=timeout,
                max_retries=retries,
            )
        case "fake":
            provider = FakeProvider(
                default_model=model, timeout_seconds=timeout, max_retries=retries
            )
        case unknown:  # pragma: no cover - the Literal makes this unreachable
            raise LLMConfigurationError(f"Unknown LLM provider: {unknown}")

    provider.validate_configuration()
    logger.info(
        "llm_provider_configured",
        provider=provider.name,
        model=provider.default_model,
        is_test_double=provider.name == "fake",
    )
    return provider
