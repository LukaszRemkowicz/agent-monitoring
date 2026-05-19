from __future__ import annotations

import io
import json
import logging

import pytest

from conf import Settings
from logging_config import JsonFormatter, PlainFormatter, configure_logging, get_logger


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

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "agent_monitoring.tests"
    assert payload["message"] == "hello world"
    assert payload["project"] == "landingpage"
    assert payload["attempt"] == 1


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
    runtime_settings = Settings({"LOG_LEVEL": "INFO", "LOG_FORMAT": "json"})

    configure_logging(runtime_settings, stream=stream)
    get_logger("tests").info("configured", extra={"project": "landingpage"})

    payload = json.loads(stream.getvalue())
    assert payload["logger"] == "agent_monitoring.tests"
    assert payload["message"] == "configured"
    assert payload["project"] == "landingpage"


def test_configure_logging_supports_pretty_colored_logs() -> None:
    stream = io.StringIO()
    runtime_settings = Settings(
        {"LOG_LEVEL": "INFO", "LOG_FORMAT": "pretty", "LOG_COLOR": "always"}
    )

    configure_logging(runtime_settings, stream=stream)
    get_logger("tests").info("configured", extra={"event": "tool_result"})

    message = stream.getvalue()
    assert "\033[32m" in message
    assert "INFO [agent_monitoring.tests] configured" in message
    assert "event='tool_result'" in message
    assert "asctime=" not in message


def test_configure_logging_rejects_unknown_format() -> None:
    runtime_settings = Settings({"LOG_LEVEL": "INFO", "LOG_FORMAT": "xml", "LOG_COLOR": "never"})

    with pytest.raises(ValueError, match="Unsupported LOG_FORMAT"):
        configure_logging(runtime_settings, stream=io.StringIO())
