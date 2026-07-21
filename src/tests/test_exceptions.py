from __future__ import annotations

import httpx

from exceptions import (
    McpClientError,
    format_exception_chain,
    format_operator_exception_message,
)


def test_exception_formatters_omit_suppressed_context() -> None:
    class HiddenProviderError(RuntimeError):
        status_code = 429
        body = {"error": {"message": "hidden provider detail"}}

    try:
        try:
            raise HiddenProviderError("hidden context")
        except HiddenProviderError:
            raise RuntimeError("public failure") from None
    except RuntimeError as error:
        assert format_exception_chain(error) == "public failure"
        assert format_operator_exception_message(error) == "public failure"


def test_operator_exception_message_uses_meaningful_cause_when_wrapper_is_empty() -> None:
    timeout = TimeoutError("deadline exceeded")
    read_timeout = httpx.ReadTimeout("")
    read_timeout.__cause__ = timeout
    error = McpClientError(
        "MCP workflow call failed: ",
        mcp_url="https://mcp.example.com/mcp",
        tool_name="list_projects",
    )
    error.__cause__ = read_timeout

    message = format_operator_exception_message(error)

    assert message == "MCP workflow call failed: ReadTimeout caused by deadline exceeded"


def test_operator_exception_message_explains_mcp_collect_logs_timeout() -> None:
    error = McpClientError(
        "MCP workflow call failed: ReadTimeout\n"
        "Caused by: TimeoutError\n"
        "Caused by: CancelledError: deadline exceeded",
        mcp_url="https://mcp.example.com/mcp",
        tool_name="collect_logs",
    )

    message = format_operator_exception_message(error)

    assert message == (
        "MCP collect_logs timed out while waiting for the MCP server to finish "
        "collecting logs. MCP was reachable; increase MCP_TIMEOUT_SECONDS or "
        "reduce the collection window/source scope, then rerun the job."
    )


def test_operator_exception_message_includes_structured_status_timeout_context() -> None:
    error = McpClientError(
        "MCP workflow call failed: ReadTimeout: status response timed out",
        mcp_url="https://mcp.example.com/mcp",
        tool_name="get_log_collection_status",
        stage="status_poll",
        session_id="workflow-session",
        timeout_seconds=90.0,
        root_cause="ReadTimeout: status response timed out",
        retry_guidance=(
            "Retry status polling with the same session ID; collection continues server-side."
        ),
    )

    message = format_operator_exception_message(error)

    assert "Stage: status_poll" in message
    assert "Tool: get_log_collection_status" in message
    assert "Session ID: workflow-session" in message
    assert "Timeout: 90 seconds" in message
    assert "Root cause: ReadTimeout: status response timed out" in message
    assert "Retry status polling with the same session ID" in message


def test_operator_exception_message_keeps_non_empty_wrapper_message() -> None:
    error = McpClientError(
        "MCP workflow call failed: All connection attempts failed",
        mcp_url="https://mcp.example.com/mcp",
        tool_name="collect_logs",
    )

    message = format_operator_exception_message(error)

    assert message == "MCP workflow call failed: All connection attempts failed"
