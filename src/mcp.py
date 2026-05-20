from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
from pydantic import ValidationError

from logging_config import get_logger
from schemas import (
    McpServiceStatus,
    McpServiceStatusResponse,
    McpToolResponse,
    StructuredContent,
    WorkflowBootstrap,
)

logger = get_logger(__name__)


class McpWorkflowClient:
    """Small JSON-RPC client for the MCP workflow endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        workflow_jwt: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.workflow_jwt = workflow_jwt
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> StructuredContent:
        """Call one MCP tool and return validated `result.structuredContent`."""

        if not self.workflow_jwt:
            raise RuntimeError("MCP_WORKFLOW_JWT is required to call the MCP workflow endpoint.")

        response: dict[str, Any] = await self._make_call(name, arguments)
        try:
            tool_response: McpToolResponse = McpToolResponse.model_validate(response)
        except ValidationError as exc:
            raise RuntimeError("MCP workflow response did not match expected shape.") from exc
        if tool_response.error is not None:
            raise RuntimeError(f"MCP workflow error: {tool_response.error.message}")
        if tool_response.result is None:
            raise RuntimeError("MCP workflow response did not include a result object.")
        logger.info(
            "MCP workflow tool call completed",
            extra={
                "event": "mcp_tool_call_done",
                "tool_name": name,
                "mcp_url": self.base_url,
            },
        )
        return tool_response.result.structured_content

    async def get_workflow_bundle(self) -> WorkflowBootstrap:
        """Return the daily log workflow bootstrap bundle from MCP."""

        structured_content: StructuredContent = await self.call_tool("analyze_daily_log_bundle")
        return WorkflowBootstrap.model_validate(structured_content.model_dump())

    async def get_service_status(self) -> McpServiceStatus:
        """Return MCP service status diagnostics."""

        response: dict[str, Any] = await self._make_call("get_mcp_service_status")
        try:
            status_response: McpServiceStatusResponse = McpServiceStatusResponse.model_validate(
                response
            )
        except ValidationError as exc:
            raise RuntimeError("MCP status response did not match expected shape.") from exc
        if status_response.error is not None:
            raise RuntimeError(f"MCP status error: {status_response.error.message}")
        if status_response.result is None:
            raise RuntimeError("MCP status response did not include a result object.")
        return status_response.result.structured_content

    async def _make_call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            logger.info(
                "calling MCP workflow tool",
                extra={
                    "event": "mcp_tool_call_start",
                    "tool_name": name,
                    "mcp_url": self.base_url,
                },
            )
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.base_url,
                    json=self._build_tool_call_payload(name, arguments),
                    headers=self._build_headers(),
                )
                response.raise_for_status()
                response_payload: Any = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "MCP workflow tool call failed",
                extra={
                    "event": "mcp_tool_call_failed",
                    "tool_name": name,
                    "mcp_url": self.base_url,
                    "error": str(exc),
                },
            )
            raise RuntimeError(f"MCP workflow call failed: {exc}") from exc
        except ValueError as exc:
            logger.warning(
                "MCP workflow response was invalid JSON",
                extra={
                    "event": "mcp_tool_call_invalid_json",
                    "tool_name": name,
                    "mcp_url": self.base_url,
                },
            )
            raise RuntimeError("MCP workflow response was not valid JSON.") from exc

        if not isinstance(response_payload, dict):
            raise RuntimeError("MCP workflow response must be a JSON object.")
        return response_payload

    @staticmethod
    def _build_tool_call_payload(
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": f"agent-monitoring-{uuid4()}",
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {},
            },
        }

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.workflow_jwt}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
