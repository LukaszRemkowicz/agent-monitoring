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
        tool_name="collect_logs",
    )
    error.__cause__ = read_timeout

    message = format_operator_exception_message(error)

    assert message == "MCP workflow call failed: ReadTimeout caused by deadline exceeded"


def test_operator_exception_message_keeps_non_empty_wrapper_message() -> None:
    error = McpClientError(
        "MCP workflow call failed: All connection attempts failed",
        mcp_url="https://mcp.example.com/mcp",
        tool_name="collect_logs",
    )

    message = format_operator_exception_message(error)

    assert message == "MCP workflow call failed: All connection attempts failed"
