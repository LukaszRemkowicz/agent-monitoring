"""Process logging configuration for the monitoring app."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from conf import Settings

LOGGER_NAME = "agent_monitoring"
LOG_FORMAT_JSON = "json"
LOG_FORMAT_PLAIN = "plain"
LOG_FORMAT_PRETTY = "pretty"

_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}
_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[35m",
}
_RESET_COLOR = "\033[0m"


def _normalize_log_value(value: Any) -> Any:
    """Convert one extra log value into a JSON-safe representation."""

    if isinstance(value, str):
        return _sanitize_message(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_normalize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_log_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_log_value(item) for key, item in value.items()}
    return _sanitize_message(str(value))


def _sanitize_message(value: str) -> str:
    """Keep logs single-line and reasonably compact."""

    sanitized = value.replace("\n", " ").replace("\r", " ")
    if len(sanitized) <= 2000:
        return sanitized
    return f"{sanitized[:1997]}..."


def _extra_log_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: _normalize_log_value(value)
        for key, value in record.__dict__.items()
        if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_")
    }


def _use_color(value: str, stream: Any) -> bool:
    if value == "always":
        return True
    if value == "never":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _record_has_exception(record: logging.LogRecord) -> bool:
    return bool(record.exc_info and record.exc_info[0] is not None)


class JsonFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _sanitize_message(record.getMessage()),
        }
        payload.update(_extra_log_fields(record))
        if _record_has_exception(record):
            exc_info = record.exc_info
            if exc_info is not None:
                payload["exception"] = self.formatException(exc_info)
        if record.stack_info:
            payload["stack"] = _sanitize_message(self.formatStack(record.stack_info))
        return json.dumps(payload, ensure_ascii=True)


class PlainFormatter(logging.Formatter):
    """Render readable plain-text logs with appended structured extras."""

    def __init__(self, *args: Any, use_color: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        original_msg = record.msg
        original_args = record.args
        original_exc_info = record.exc_info
        original_exc_text = record.exc_text
        record.msg = _sanitize_message(record.getMessage())
        record.args = ()
        if not _record_has_exception(record):
            record.exc_info = None
            record.exc_text = None
        try:
            base_message = super().format(record)
        finally:
            record.msg = original_msg
            record.args = original_args
            record.exc_info = original_exc_info
            record.exc_text = original_exc_text

        if self.use_color:
            color = _LEVEL_COLORS.get(record.levelname, "")
            if color:
                base_message = f"{color}{base_message}{_RESET_COLOR}"

        extra_parts = [
            f"{key}={value!r}" for key, value in sorted(_extra_log_fields(record).items())
        ]
        if not extra_parts:
            return base_message
        return f"{base_message} {' '.join(extra_parts)}"


def configure_logging(settings: Settings, stream: Any | None = None) -> logging.Logger:
    """Configure process logging and return the project logger."""

    output = stream or sys.stderr
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(settings.LOG_LEVEL.upper())
    logger.propagate = False

    handler = logging.StreamHandler(output)
    log_format = settings.LOG_FORMAT.lower()
    if log_format == LOG_FORMAT_JSON:
        handler.setFormatter(JsonFormatter())
    elif log_format in {LOG_FORMAT_PLAIN, LOG_FORMAT_PRETTY}:
        handler.setFormatter(
            PlainFormatter(
                fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
                use_color=_use_color(settings.LOG_COLOR.lower(), output),
            )
        )
    else:
        raise ValueError(f"Unsupported LOG_FORMAT: {settings.LOG_FORMAT}")

    logger.addHandler(handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the base project logger or a namespaced child logger."""

    logger = logging.getLogger(LOGGER_NAME)
    if name is None:
        return logger
    return logger.getChild(name)
