"""Application exception hierarchy.

Every exception carries the HTTP status and a stable machine-readable `error_code`
that clients can branch on. The message is assumed to be user-safe; anything
sensitive belongs in the log record, never in `message` (brief §60).
"""

from __future__ import annotations

from typing import Any


class InsightAgentError(Exception):
    """Base class for every error this application raises deliberately."""

    status_code: int = 500
    error_code: str = "internal_error"
    default_message: str = "An internal error occurred."

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


# --- client errors ----------------------------------------------------------


class AuthenticationError(InsightAgentError):
    status_code = 401
    error_code = "authentication_failed"
    default_message = "Invalid credentials."


class AuthorizationError(InsightAgentError):
    status_code = 403
    error_code = "forbidden"
    default_message = "You do not have permission to perform this action."


class NotFoundError(InsightAgentError):
    status_code = 404
    error_code = "not_found"
    default_message = "The requested resource was not found."


class ConflictError(InsightAgentError):
    status_code = 409
    error_code = "conflict"
    default_message = "The resource already exists."


class ValidationError(InsightAgentError):
    status_code = 422
    error_code = "validation_error"
    default_message = "The request payload is invalid."


class RateLimitError(InsightAgentError):
    status_code = 429
    error_code = "rate_limited"
    default_message = "Too many requests."


# --- domain errors ----------------------------------------------------------
# Declared now so later phases raise from a stable, already-handled hierarchy
# rather than introducing ad-hoc exception types alongside their features.


class LLMProviderError(InsightAgentError):
    status_code = 502
    error_code = "llm_provider_error"
    default_message = "The language model provider failed to respond."


class RetrievalError(InsightAgentError):
    status_code = 500
    error_code = "retrieval_error"
    default_message = "Evidence retrieval failed."


class ToolExecutionError(InsightAgentError):
    status_code = 500
    error_code = "tool_execution_error"
    default_message = "An agent tool failed to execute."


class SQLValidationError(InsightAgentError):
    status_code = 400
    error_code = "sql_validation_error"
    default_message = "The generated SQL failed safety validation."


class DocumentProcessingError(InsightAgentError):
    status_code = 422
    error_code = "document_processing_error"
    default_message = "The document could not be processed."


class EvaluationError(InsightAgentError):
    status_code = 500
    error_code = "evaluation_error"
    default_message = "The evaluation run failed."


class ServiceUnavailableError(InsightAgentError):
    status_code = 503
    error_code = "service_unavailable"
    default_message = "A required dependency is unavailable."
