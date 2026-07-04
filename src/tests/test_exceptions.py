from __future__ import annotations

import httpx

from exceptions import McpClientError, format_operator_exception_message


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


def test_operator_exception_message_keeps_non_empty_wrapper_message() -> None:
    error = McpClientError(
        "MCP workflow call failed: All connection attempts failed",
        mcp_url="https://mcp.example.com/mcp",
        tool_name="collect_logs",
    )

    message = format_operator_exception_message(error)

    assert message == "MCP workflow call failed: All connection attempts failed"
