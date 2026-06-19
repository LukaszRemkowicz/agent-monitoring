from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas import CollectLogsArtifact, LogAnalysisPreparedPrompt, WorkflowBootstrap


def format_exception_chain(exc: BaseException) -> str:
    """Return a compact message with chained exception details."""

    messages: list[str] = [_format_exception_message(exc, include_type=False)]
    current: BaseException | None = exc.__cause__ or exc.__context__
    while current is not None:
        messages.append(_format_exception_message(current, include_type=True))
        current = current.__cause__ or current.__context__
    return "\nCaused by: ".join(messages)


def _format_exception_message(exc: BaseException, *, include_type: bool) -> str:
    current_type: str = exc.__class__.__name__
    provider_message: str | None = _format_provider_status_message(exc)
    message: str = provider_message or str(exc) or current_type
    if include_type and message != current_type:
        return f"{current_type}: {message}"
    return message


def _format_provider_status_message(exc: BaseException) -> str | None:
    status_code: object = getattr(exc, "status_code", None)
    response: object = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    message: str | None = _extract_provider_error_message(getattr(exc, "body", None))
    if message is None:
        parsed_status_code, parsed_message = _parse_provider_error_string(str(exc))
        status_code = status_code or parsed_status_code
        message = parsed_message

    if status_code is None or not message:
        return None
    return f"Status {status_code}: {message}"


def _extract_provider_error_message(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    error: object = body.get("error")
    if not isinstance(error, dict):
        return None
    message: object = error.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    return message.strip()


def _parse_provider_error_string(value: str) -> tuple[str | None, str | None]:
    match: re.Match[str] | None = re.search(r"Error code:\s*(\d+)\s*-\s*(\{.*\})", value)
    if match is None:
        return None, None
    try:
        payload: object = ast.literal_eval(match.group(2))
    except (SyntaxError, ValueError):
        return match.group(1), None
    return match.group(1), _extract_provider_error_message(payload)


class McpClientError(RuntimeError):
    """Raised when an MCP JSON-RPC call cannot complete or validate."""

    def __init__(
        self,
        message: str,
        *,
        mcp_url: str = "",
        tool_name: str = "",
        hint: str = "",
    ) -> None:
        super().__init__(message)
        self.mcp_url = mcp_url
        self.tool_name = tool_name
        self.hint = hint


class PrivateMonitoringContextError(RuntimeError):
    """Raised when the mandatory project context prompt is missing or invalid."""

    def __init__(self, message: str, *, context_path: str = "") -> None:
        super().__init__(message)
        self.context_path = context_path


class LogAnalysisAgentError(RuntimeError):
    """Raised when the agent fails after collecting partial workflow context."""

    def __init__(
        self,
        message: str,
        *,
        workflow: WorkflowBootstrap | None = None,
        collect_logs: CollectLogsArtifact | None = None,
        prompt: LogAnalysisPreparedPrompt | None = None,
    ) -> None:
        super().__init__(message)
        self.workflow = workflow
        self.collect_logs = collect_logs
        self.prompt = prompt


class LogAnalysisComparisonMissingException(RuntimeError):
    """Raised when comparison-only behavior is called without a comparison object."""


class LogAnalysisHistoryComparisonServiceMissingException(RuntimeError):
    """Raised when history comparison is enabled without its comparison service."""
