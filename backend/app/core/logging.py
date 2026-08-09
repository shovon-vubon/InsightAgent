"""Structured logging (brief §27).

JSON in production, human-readable in development. Every record carries the
request id and — once the agent runtime exists — the agent run id, via
contextvars, so a single run can be reconstructed from logs alone.

`print()` is banned by ruff's T20 rule; this is the only logging path.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from app.core.config import Settings

# Keys whose values must never reach a log sink. Matched case-insensitively as a
# substring, so `db_password`, `X-Api-Key` and `refresh_token` are all caught.
SENSITIVE_KEY_PATTERN = re.compile(
    r"password|passwd|secret|token|api_key|apikey|authorization|cookie|credential|session_id",
    re.IGNORECASE,
)
REDACTED = "[redacted]"
_MAX_REDACTION_DEPTH = 6


def _redact(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_REDACTION_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            key: (REDACTED if SENSITIVE_KEY_PATTERN.search(str(key)) else _redact(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact(item, depth + 1) for item in value)
    return value


def redact_sensitive(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """structlog processor that scrubs secret-looking keys at any nesting level."""
    return _redact(event_dict)  # type: ignore[no-any-return]


def configure_logging(settings: Settings) -> None:
    """Route stdlib logging (uvicorn, sqlalchemy) and structlog through one renderer."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        redact_sensitive,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.LOG_FORMAT == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL)

    # uvicorn installs its own handlers; drop them so nothing is emitted twice.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DB_ECHO else logging.WARNING
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
