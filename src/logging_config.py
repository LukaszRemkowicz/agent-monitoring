"""Process logging configuration for the monitoring app."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
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
_JSON_KEY_COLOR = "\033[36m"
_JSON_STRING_COLOR = "\033[32m"
_JSON_NUMBER_COLOR = "\033[33m"
_JSON_BOOL_COLOR = "\033[35m"
_JSON_PUNCTUATION_COLOR = "\033[90m"
_JSON_KEY_PATTERN = re.compile(r'^(\s*)("[^"]+": )(.*)$')
_JSON_STRING_PATTERN = re.compile(r'^("[^"]*")(,?)$')
_JSON_NUMBER_PATTERN = re.compile(r"^(-?\d+(?:\.\d+)?)(,?)$")
_JSON_BOOL_PATTERN = re.compile(r"^(true|false|null)(,?)$")


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
    fields = {
        key: _normalize_log_value(value)
        for key, value in record.__dict__.items()
        if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_")
    }
    return fields


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

    def __init__(
        self,
        *args: Any,
        indent: int | None = None,
        use_color: bool = False,
        timezone: str = "Europe/Warsaw",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.indent = indent
        self.use_color = use_color
        self.timezone = ZoneInfo(timezone)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, self.timezone).isoformat(),
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
        message = json.dumps(payload, ensure_ascii=True, indent=self.indent)
        if self.use_color:
            return _colorize_json(message)
        return message


def _colorize_json(message: str) -> str:
    return "\n".join(_colorize_json_line(line) for line in message.splitlines())


def _colorize_json_line(line: str) -> str:
    if line in {"{", "}"}:
        return f"{_JSON_PUNCTUATION_COLOR}{line}{_RESET_COLOR}"

    key_match = _JSON_KEY_PATTERN.match(line)
    if key_match:
        indent, key, value = key_match.groups()
        return f"{indent}{_JSON_KEY_COLOR}{key}{_RESET_COLOR}{_colorize_json_value(value)}"

    return _colorize_json_value(line)


def _colorize_json_value(value: str) -> str:
    stripped = value.strip()
    leading = value[: len(value) - len(value.lstrip())]
    if stripped in {"{", "}"}:
        return f"{leading}{_JSON_PUNCTUATION_COLOR}{stripped}{_RESET_COLOR}"

    string_match = _JSON_STRING_PATTERN.match(stripped)
    if string_match:
        string_value, comma = string_match.groups()
        return f"{leading}{_JSON_STRING_COLOR}{string_value}{_RESET_COLOR}{comma}"

    number_match = _JSON_NUMBER_PATTERN.match(stripped)
    if number_match:
        number_value, comma = number_match.groups()
        return f"{leading}{_JSON_NUMBER_COLOR}{number_value}{_RESET_COLOR}{comma}"

    bool_match = _JSON_BOOL_PATTERN.match(stripped)
    if bool_match:
        bool_value, comma = bool_match.groups()
        return f"{leading}{_JSON_BOOL_COLOR}{bool_value}{_RESET_COLOR}{comma}"

    return value


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
        handler.setFormatter(JsonFormatter(timezone=settings.LOG_TIMEZONE))
    elif log_format == LOG_FORMAT_PRETTY:
        handler.setFormatter(
            JsonFormatter(
                indent=2,
                use_color=_use_color(settings.LOG_COLOR.lower(), output),
                timezone=settings.LOG_TIMEZONE,
            )
        )
    elif log_format == LOG_FORMAT_PLAIN:
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

    file_handler = _build_file_handler(
        logs_dir=settings.LOGS_DIR,
        timezone=settings.LOG_TIMEZONE,
    )
    logger.addHandler(file_handler)
    return logger


def _build_file_handler(
    *,
    logs_dir: str,
    timezone: str,
) -> logging.FileHandler:
    """Return a JSON file handler for today's dated log file."""

    log_date = datetime.now(ZoneInfo(timezone)).date().isoformat()
    path = Path(logs_dir) / f"{log_date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter(timezone=timezone))
    return handler


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the base project logger or a namespaced child logger."""

    logger = logging.getLogger(LOGGER_NAME)
    if name is None:
        return logger
    return logger.getChild(name)
