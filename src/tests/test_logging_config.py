from __future__ import annotations

import io
import json
import logging
import re

import pytest

from conf import Settings
from logging_config import JsonFormatter, PlainFormatter, configure_logging, get_logger

ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="agent_monitoring.tests",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello\nworld",
        args=(),
        exc_info=None,
    )
    record.__dict__.update(extra)
    return record


def test_json_formatter_outputs_single_line_structured_payload() -> None:
    record = make_record(project="landingpage", attempt=1)
    record.created = 1780611503.850642

    message = JsonFormatter().format(record)
    payload = json.loads(message)

    assert "\n" not in message
    assert payload["level"] == "INFO"
    assert payload["logger"] == "agent_monitoring.tests"
    assert payload["message"] == "hello world"
    assert payload["timestamp"] == "2026-06-05T00:18:23.850642+02:00"
    assert payload["project"] == "landingpage"
    assert payload["attempt"] == 1


def test_json_formatter_can_render_pretty_json() -> None:
    record = make_record(project="landingpage", attempt=1)

    message = JsonFormatter(indent=2).format(record)
    payload = json.loads(message)

    assert message.startswith("{\n")
    assert '\n  "level": "INFO"' in message
    assert payload["project"] == "landingpage"


def test_json_formatter_can_render_colored_pretty_json() -> None:
    record = make_record(project="landingpage", attempt=1, force=False)

    message = JsonFormatter(indent=2, use_color=True).format(record)
    payload = json.loads(ANSI_PATTERN.sub("", message))

    assert "\033[36m" in message
    assert "\033[32m" in message
    assert "\033[33m" in message
    assert "\033[35m" in message
    assert payload["project"] == "landingpage"
    assert payload["attempt"] == 1
    assert payload["force"] is False


def test_plain_formatter_appends_sorted_extra_fields() -> None:
    record = make_record(project="landingpage", attempt=1)

    message = PlainFormatter(fmt="%(levelname)s %(message)s").format(record)

    assert message == "INFO hello world attempt=1 project='landingpage'"


def test_plain_formatter_can_color_level_line() -> None:
    record = make_record()

    message = PlainFormatter(fmt="%(levelname)s %(message)s", use_color=True).format(record)

    assert message.startswith("\033[32mINFO hello world")
    assert message.endswith("\033[0m")


def test_plain_formatter_ignores_empty_exception_info() -> None:
    record = make_record(exc_info=(None, None, None))

    message = PlainFormatter(fmt="%(levelname)s %(message)s").format(record)

    assert message == "INFO hello world"
    assert "NoneType: None" not in message


def test_json_formatter_ignores_empty_exception_info() -> None:
    record = make_record(exc_info=(None, None, None))

    payload = json.loads(JsonFormatter().format(record))

    assert "exception" not in payload


def test_configure_logging_supports_json_and_child_loggers() -> None:
    stream = io.StringIO()
    settings = Settings(
        {
            "LOG_LEVEL": "INFO",
            "LOG_FORMAT": "json",
            "LOG_TIMEZONE": "Europe/Warsaw",
        }
    )

    configure_logging(settings, stream=stream)
    get_logger("tests").info("configured", extra={"project": "landingpage"})

    payload = json.loads(stream.getvalue())
    assert payload["logger"] == "agent_monitoring.tests"
    assert payload["message"] == "configured"
    assert payload["project"] == "landingpage"
    assert payload["timestamp"].endswith("+02:00")


def test_configure_logging_supports_pretty_json_logs() -> None:
    stream = io.StringIO()
    settings = Settings(
        {
            "LOG_LEVEL": "INFO",
            "LOG_FORMAT": "pretty",
            "LOG_COLOR": "always",
            "LOG_TIMEZONE": "Europe/Warsaw",
        }
    )

    configure_logging(settings, stream=stream)
    get_logger("tests").info("configured", extra={"event": "tool_result"})

    message = stream.getvalue()
    clean_message = ANSI_PATTERN.sub("", message)
    payload = json.loads(clean_message)
    assert clean_message.startswith("{\n")
    assert "\033[36m" in message
    assert payload["logger"] == "agent_monitoring.tests"
    assert payload["message"] == "configured"
    assert payload["event"] == "tool_result"


def test_configure_logging_rejects_unknown_format() -> None:
    settings = Settings({"LOG_LEVEL": "INFO", "LOG_FORMAT": "xml", "LOG_COLOR": "never"})

    with pytest.raises(ValueError, match="Unsupported LOG_FORMAT"):
        configure_logging(settings, stream=io.StringIO())
