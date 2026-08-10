"""Provider error taxonomy.

Vendor SDKs each raise their own exception hierarchy. Providers translate those
into these types so that retry logic, fallback (Phase 12), and API error mapping
are written once against a stable set.

The distinction that matters is **retryable or not**: a rate limit or a transient
5xx is worth another attempt, a bad API key or an oversized prompt never is.
"""

from __future__ import annotations

from app.core.exceptions import LLMProviderError

# Re-exported so this module is the single import site for the whole taxonomy.
__all__ = [
    "RETRYABLE_ERRORS",
    "LLMAuthenticationError",
    "LLMBadRequestError",
    "LLMConfigurationError",
    "LLMContentFilterError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMServiceUnavailableError",
    "LLMTimeoutError",
    "is_retryable",
]


class LLMRateLimitError(LLMProviderError):
    error_code = "llm_rate_limited"
    default_message = "The language model provider is rate limiting requests."


class LLMTimeoutError(LLMProviderError):
    error_code = "llm_timeout"
    default_message = "The language model provider did not respond in time."


class LLMServiceUnavailableError(LLMProviderError):
    error_code = "llm_unavailable"
    default_message = "The language model provider is temporarily unavailable."


class LLMAuthenticationError(LLMProviderError):
    status_code = 500  # a misconfigured server key is our fault, not the caller's
    error_code = "llm_authentication_failed"
    default_message = "The language model provider rejected our credentials."


class LLMBadRequestError(LLMProviderError):
    error_code = "llm_bad_request"
    default_message = "The request to the language model provider was invalid."


class LLMContentFilterError(LLMProviderError):
    error_code = "llm_content_filtered"
    default_message = "The language model provider refused to process this content."


class LLMConfigurationError(LLMProviderError):
    error_code = "llm_configuration_error"
    default_message = "The language model provider is not configured correctly."


#: Failures worth another attempt. Everything else fails fast — retrying a
#: rejected credential or a malformed request only wastes the user's latency.
RETRYABLE_ERRORS: tuple[type[LLMProviderError], ...] = (
    LLMRateLimitError,
    LLMTimeoutError,
    LLMServiceUnavailableError,
)


def is_retryable(error: LLMProviderError) -> bool:
    if type(error) is LLMProviderError:
        # The generic base is raised for unclassified transport failures.
        return True
    return isinstance(error, RETRYABLE_ERRORS)
