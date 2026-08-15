"""Structured logging.

Log records carry an `event` key drawn from `openpdn.application.events` plus
structured context (`board_id`, `study_id`, `solver`, `duration_seconds`, ...).
The JSON renderer emits one object per line for ingestion; the text renderer is
for humans at a terminal.

Two rules matter more than formatting:

* never log credentials -- keys that look secret are redacted here as a
  backstop, but the real fix is not to pass them;
* never log full PCB geometry -- boards are confidential and enormous. Log
  counts and identifiers.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING, Any, Final

from openpdn.infrastructure.config import LogFormat, LogLevel

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Attributes present on every LogRecord; anything else is caller context.
_STANDARD_RECORD_FIELDS: Final = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

#: Context keys whose values are replaced before they reach a log sink.
_REDACTED_KEY_PARTS: Final = ("password", "secret", "token", "api_key", "credential")

_REDACTED: Final = "***redacted***"


def _context_of(record: logging.LogRecord) -> dict[str, Any]:
    """Return the caller-supplied `extra` fields of `record`, redacted."""
    context: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
            continue
        lowered = key.lower()
        context[key] = _REDACTED if any(part in lowered for part in _REDACTED_KEY_PARTS) else value
    return context


class JsonLogFormatter(logging.Formatter):
    """Renders one JSON object per record."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a single-line JSON object."""
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_context_of(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class TextLogFormatter(logging.Formatter):
    """Renders a readable console line with trailing `key=value` context."""

    def __init__(self) -> None:
        """Configure the base line layout."""
        super().__init__(fmt="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        """Return the record with its structured context appended."""
        line = super().format(record)
        context = _context_of(record)
        context.pop("event", None)  # already the message for openPDN event logs
        if context:
            rendered = " ".join(f"{key}={value}" for key, value in sorted(context.items()))
            line = f"{line} [{rendered}]"
        return line


def configure_logging(
    level: LogLevel = LogLevel.INFO,
    log_format: LogFormat = LogFormat.TEXT,
) -> None:
    """Install openPDN's log handler on the root logger.

    Idempotent: repeated calls replace the handler rather than stacking
    duplicates, which matters because both the API lifespan and the CLI
    configure logging.
    """
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonLogFormatter() if log_format is LogFormat.JSON else TextLogFormatter())
    handler.set_name("openpdn")

    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == "openpdn"]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.value)


def log_event(
    logger: logging.Logger,
    event: str,
    /,
    level: int = logging.INFO,
    **context: Any,
) -> None:
    """Emit a structured event.

    Convenience wrapper over `logger.log(level, event, extra={...})`; the
    application layer uses stdlib logging directly so it stays free of this
    package.
    """
    logger.log(level, event, extra={"event": event, **context})


def bind_context(**context: Any) -> Mapping[str, Any]:
    """Build an `extra` mapping for a log call. Kept for call-site readability."""
    return dict(context)
