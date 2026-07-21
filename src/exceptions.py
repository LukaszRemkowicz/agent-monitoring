from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas import CollectLogsArtifact, LogAnalysisPreparedPrompt, WorkflowBootstrap


def format_exception_chain(exc: BaseException) -> str:
    """Return a compact message with chained exception details."""

    messages: list[str] = [_format_exception_message(exc, include_type=False)]
    current: BaseException | None = _next_exception_in_chain(exc)
    while current is not None:
        messages.append(_format_exception_message(current, include_type=True))
        current = _next_exception_in_chain(current)
    return "\nCaused by: ".join(messages)


def format_operator_exception_message(exc: BaseException) -> str:
    """Return a short, actionable failure summary for operator emails."""

    chain: list[BaseException] = list(_iter_exception_chain(exc))
    mcp_timeout = next(
        (
            chain_exc
            for chain_exc in chain
            if isinstance(chain_exc, McpClientError) and chain_exc.stage
        ),
        None,
    )
    if mcp_timeout is not None:
        return _format_mcp_timeout_message(mcp_timeout)
    for chain_exc in chain:
        provider_message: str | None = _format_provider_status_message(chain_exc)
        if provider_message is not None:
            return provider_message

    full_message: str = "\nCaused by: ".join(str(chain_exc) for chain_exc in chain)
    if _looks_like_mcp_collect_logs_timeout(chain):
        return (
            "MCP collect_logs timed out while waiting for the MCP server to finish "
            "collecting logs. MCP was reachable; increase MCP_TIMEOUT_SECONDS or "
            "reduce the collection window/source scope, then rerun the job."
        )

    mcp_schema_field: str | None = _extract_mcp_schema_field(full_message)
    if mcp_schema_field is not None:
        return (
            "MCP collect_logs returned a response field this monitoring worker did not "
            f"recognize: {mcp_schema_field}. Update the local MCP schema contract, then "
            "rerun the job."
        )

    if _looks_like_datetime_serialization_error(full_message):
        return (
            "The monitoring worker tried to save a timestamp as text. Update the local "
            "database write path, then rerun the job."
        )

    return _single_line(_operator_fallback_message(chain), max_length=280)


def get_mcp_failure_context(exc: BaseException) -> dict[str, str | float]:
    """Return structured MCP timeout details for the operator email."""

    for chain_exc in _iter_exception_chain(exc):
        if isinstance(chain_exc, McpClientError) and chain_exc.stage:
            context: dict[str, str | float] = {
                "stage": chain_exc.stage,
                "tool_name": chain_exc.tool_name,
                "session_id": chain_exc.session_id or "not available",
                "root_cause": chain_exc.root_cause or str(chain_exc),
                "retry_guidance": chain_exc.retry_guidance,
            }
            if chain_exc.timeout_seconds is not None:
                context["timeout_seconds"] = chain_exc.timeout_seconds
            return context
    return {}


def _format_mcp_timeout_message(error: McpClientError) -> str:
    timeout = (
        f"{error.timeout_seconds:g} seconds"
        if error.timeout_seconds is not None
        else "not available"
    )
    return (
        f"Stage: {error.stage}; Tool: {error.tool_name or 'not available'}; "
        f"Session ID: {error.session_id or 'not available'}; Timeout: {timeout}; "
        f"Root cause: {error.root_cause or str(error)}; "
        f"Retry guidance: {error.retry_guidance or 'Inspect raw diagnostics before retrying.'}"
    )


def _iter_exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None:
        chain.append(current)
        current = _next_exception_in_chain(current)
    return chain


def _next_exception_in_chain(exc: BaseException) -> BaseException | None:
    if exc.__cause__ is not None:
        return exc.__cause__
    if exc.__suppress_context__:
        return None
    return exc.__context__


def _format_exception_message(exc: BaseException, *, include_type: bool) -> str:
    current_type: str = exc.__class__.__name__
    provider_message: str | None = _format_provider_status_message(exc)
    message: str = provider_message or str(exc) or current_type
    if include_type and message != current_type:
        return f"{current_type}: {message}"
    return message


def _operator_fallback_message(chain: list[BaseException]) -> str:
    first_message: str = _format_exception_message(chain[0], include_type=False)
    if not _looks_like_empty_wrapper_message(first_message):
        return first_message

    cause_messages: list[str] = [
        _format_exception_message(chain_exc, include_type=False)
        for chain_exc in chain[1:]
        if _format_exception_message(chain_exc, include_type=False).strip()
    ]
    if not cause_messages:
        return first_message

    return f"{first_message} {' caused by '.join(cause_messages)}"


def _looks_like_empty_wrapper_message(message: str) -> bool:
    normalized: str = message.strip()
    return normalized in {
        "MCP workflow call failed:",
        "MCP call failed:",
    }


def _looks_like_mcp_collect_logs_timeout(chain: list[BaseException]) -> bool:
    if not chain:
        return False
    first = chain[0]
    tool_name = getattr(first, "tool_name", None)
    if tool_name != "collect_logs":
        return False
    full_message = "\n".join(_format_exception_message(exc, include_type=True) for exc in chain)
    return "ReadTimeout" in full_message or "deadline exceeded" in full_message


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


def _extract_mcp_schema_field(message: str) -> str | None:
    if "MCP collect_logs response did not match expected shape" not in message:
        return None
    match: re.Match[str] | None = re.search(r"\.([A-Za-z_][A-Za-z0-9_]*): Extra inputs", message)
    if match is None:
        return None
    return match.group(1)


def _looks_like_datetime_serialization_error(message: str) -> bool:
    return "datetime.date or datetime.datetime instance" in message and "got 'str'" in message


def _single_line(value: str, *, max_length: int) -> str:
    collapsed: str = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) <= max_length:
        return collapsed
    return f"{collapsed[: max_length - 1].rstrip()}..."


class McpClientError(RuntimeError):
    """Raised when an MCP JSON-RPC call cannot complete or validate."""

    def __init__(
        self,
        message: str,
        *,
        mcp_url: str = "",
        tool_name: str = "",
        hint: str = "",
        stage: str = "",
        session_id: str = "",
        timeout_seconds: float | None = None,
        root_cause: str = "",
        retry_guidance: str = "",
    ) -> None:
        super().__init__(message)
        self.mcp_url = mcp_url
        self.tool_name = tool_name
        self.hint = hint
        self.stage = stage
        self.session_id = session_id
        self.timeout_seconds = timeout_seconds
        self.root_cause = root_cause
        self.retry_guidance = retry_guidance


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
