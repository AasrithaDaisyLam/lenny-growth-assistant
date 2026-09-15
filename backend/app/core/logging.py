"""
Structured logging.

JSON logs in production, human-readable in local dev, and a correlation id on
every line so a single request can be traced end to end. Model, retrieval,
database, and artifact failures are all required to be diagnosable, which means
each needs a distinct event name rather than a generic "error".
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.contextvars import merge_contextvars

from app.config import settings

# Keys whose values are never written to a log line.
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "anthropic_api_key",
    "internal_tool_token",
    "password",
    "postgres_password",
    "secret",
    "token",
}

REDACTED = "[redacted]"


def _redact(_logger, _method_name, event_dict: dict) -> dict:
    """Strip credentials by key name, including nested dicts."""
    for key in list(event_dict):
        lowered = key.lower()
        if any(s in lowered for s in SENSITIVE_KEYS):
            event_dict[key] = REDACTED
        elif isinstance(event_dict[key], dict):
            event_dict[key] = _redact(_logger, _method_name, dict(event_dict[key]))
    return event_dict


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared: list = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.environment == "local":
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for noisy in ("httpx", "httpcore", "asyncpg", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def get_logger(name: str = "app"):
    return structlog.get_logger(name)
