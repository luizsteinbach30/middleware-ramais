"""Structured logging configuration based on structlog.

Use ``logger.info("event_name", **context)`` everywhere. Never f-string event
names. The ``redact_secrets`` processor masks well-known sensitive keys before
the log line is rendered (defense in depth — callers should also avoid passing
secrets to the logger).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict

_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "x-csrf-token",
    "uscall_token",
    "webhook_token",
    "api_key",
    "apikey",
}


def redact_secrets(_logger: object, _name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***"
    return event_dict


def persist_warn_error(_logger: object, name: str, event_dict: EventDict) -> EventDict:
    """Side-effect: copy WARN/ERROR records into the ``system_logs`` table."""
    level = event_dict.get("level") or name
    if not level or level.lower() not in ("warning", "warn", "error", "critical"):
        return event_dict
    try:
        import json
        from datetime import UTC, datetime

        from middleware_monitor.core.db import session_factory
        from middleware_monitor.core.models import SystemLog

        ctx = {k: v for k, v in event_dict.items() if k not in ("event", "level", "timestamp")}
        with session_factory() as db:
            db.add(
                SystemLog(
                    timestamp=datetime.now(UTC).replace(tzinfo=None),
                    level=str(level).upper(),
                    module=str(event_dict.get("module") or name or "app"),
                    message=str(event_dict.get("event") or ""),
                    context=json.dumps(ctx, default=str)[:8000] if ctx else None,
                )
            )
            db.commit()
    except Exception:
        # Never crash the app on a logging failure.
        pass
    return event_dict


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_secrets,
        persist_warn_error,
    ]
    if json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
