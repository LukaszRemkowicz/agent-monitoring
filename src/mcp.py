from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import httpx
from pydantic import ValidationError

from exceptions import McpClientError, format_exception_chain
from logging_config import get_logger
from schemas import (
    CollectLogsArtifact,
    McpCollectLogsResponse,
    McpGenericToolResponse,
    McpProjectManifestListResponse,
    McpReadResourceResponse,
    McpServiceStatus,
    McpServiceStatusResponse,
    McpToolName,
    McpToolResponse,
    McpToolResultError,
    ProjectManifestSummary,
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
        workflow_jwt: str = "",
        keycloak_url: str = "",
        keycloak_client_id: str = "",
        keycloak_client_secret: str = "",
        token_refresh_margin_seconds: int = 60,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.workflow_jwt = workflow_jwt
        self.keycloak_url = keycloak_url.rstrip("/")
        self.keycloak_client_id = keycloak_client_id
        self.keycloak_client_secret = keycloak_client_secret
        self.token_refresh_margin_seconds = token_refresh_margin_seconds
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._cached_workflow_jwt = ""
        self._cached_workflow_jwt_expires_at = 0.0

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> StructuredContent:
        """Call one MCP tool and return validated `result.structuredContent`."""

        self._raise_if_auth_missing(name)

        response: dict[str, Any] = await self._make_call(name, arguments)
        self._raise_tool_result_error_if_present(response, name)
        try:
            tool_response: McpToolResponse = McpToolResponse.model_validate(response)
        except ValidationError as exc:
            raise McpClientError(
                self._format_validation_error(
                    "MCP workflow response did not match expected shape.",
                    exc,
                ),
                mcp_url=self.base_url,
                tool_name=name,
            ) from exc
        if tool_response.error is not None:
            raise McpClientError(
                f"MCP workflow error: {tool_response.error.message}",
                mcp_url=self.base_url,
                tool_name=name,
            )
        if tool_response.result is None:
            raise McpClientError(
                "MCP workflow response did not include a result object.",
                mcp_url=self.base_url,
                tool_name=name,
            )
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

        structured_content: StructuredContent = await self.call_tool(
            McpToolName.ANALYZE_DAILY_LOG_BUNDLE
        )
        return WorkflowBootstrap.model_validate(structured_content.model_dump())

    async def get_sitemap_workflow_bundle(self) -> WorkflowBootstrap:
        """Return the sitemap workflow bootstrap bundle from MCP."""

        structured_content: StructuredContent = await self.call_tool(
            McpToolName.ANALYZE_SITEMAP_BUNDLE
        )
        return WorkflowBootstrap.model_validate(structured_content.model_dump())

    async def collect_logs(
        self,
        *,
        since: str,
        until: str,
    ) -> CollectLogsArtifact:
        """Collect the 24h workflow log artifact for all JWT-authorized projects."""

        response: dict[str, Any] = await self._make_call(
            McpToolName.COLLECT_LOGS,
            {"since": since, "until": until},
        )
        self._raise_tool_result_error_if_present(response, McpToolName.COLLECT_LOGS)
        try:
            collect_logs_response: McpCollectLogsResponse = McpCollectLogsResponse.model_validate(
                response
            )
        except ValidationError as exc:
            raise McpClientError(
                self._format_validation_error(
                    "MCP collect_logs response did not match expected shape.",
                    exc,
                ),
                mcp_url=self.base_url,
                tool_name=McpToolName.COLLECT_LOGS,
            ) from exc
        if collect_logs_response.error is not None:
            raise McpClientError(
                f"MCP collect_logs error: {collect_logs_response.error.message}",
                mcp_url=self.base_url,
                tool_name=McpToolName.COLLECT_LOGS,
            )
        if collect_logs_response.result is None:
            raise McpClientError(
                "MCP collect_logs response did not include a result object.",
                mcp_url=self.base_url,
                tool_name=McpToolName.COLLECT_LOGS,
            )
        return collect_logs_response.result.structured_content

    async def list_projects(self) -> list[ProjectManifestSummary]:
        """Return projects available to the authenticated MCP caller."""

        response: dict[str, Any] = await self._make_call(McpToolName.LIST_PROJECTS)
        self._raise_tool_result_error_if_present(response, McpToolName.LIST_PROJECTS)
        try:
            projects_response: McpProjectManifestListResponse = (
                McpProjectManifestListResponse.model_validate(response)
            )
        except ValidationError as exc:
            raise McpClientError(
                self._format_validation_error(
                    "MCP list_projects response did not match expected shape.",
                    exc,
                ),
                mcp_url=self.base_url,
                tool_name=McpToolName.LIST_PROJECTS,
            ) from exc
        if projects_response.error is not None:
            raise McpClientError(
                f"MCP list_projects error: {projects_response.error.message}",
                mcp_url=self.base_url,
                tool_name=McpToolName.LIST_PROJECTS,
            )
        if projects_response.result is None:
            raise McpClientError(
                "MCP list_projects response did not include a result object.",
                mcp_url=self.base_url,
                tool_name=McpToolName.LIST_PROJECTS,
            )
        return projects_response.result.structured_content.result

    async def get_service_status(self) -> McpServiceStatus:
        """Return MCP service status diagnostics."""

        response: dict[str, Any] = await self._make_call("get_mcp_service_status")
        self._raise_tool_result_error_if_present(response, "get_mcp_service_status")
        try:
            status_response: McpServiceStatusResponse = McpServiceStatusResponse.model_validate(
                response
            )
        except ValidationError as exc:
            raise McpClientError(
                self._format_validation_error(
                    "MCP status response did not match expected shape.",
                    exc,
                ),
                mcp_url=self.base_url,
                tool_name="get_mcp_service_status",
            ) from exc
        if status_response.error is not None:
            raise McpClientError(
                f"MCP status error: {status_response.error.message}",
                mcp_url=self.base_url,
                tool_name="get_mcp_service_status",
            )
        if status_response.result is None:
            raise McpClientError(
                "MCP status response did not include a result object.",
                mcp_url=self.base_url,
                tool_name="get_mcp_service_status",
            )
        return status_response.result.structured_content

    async def call_deterministic_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute one MCP-owned analysis tool requested by the LLM loop.

        The monitoring agent lets the LLM decide which advertised MCP tool it
        needs next, but the tool execution itself stays outside the LLM. This
        method is the narrow boundary for that handoff: it sends the validated
        tool name and arguments through the MCP JSON-RPC transport, checks the
        generic tool-response envelope, converts MCP errors into
        ``McpClientError``, and returns only the deterministic
        ``structuredContent`` payload.

        Keeping this as a separate client method makes the workflow boundary
        explicit. The LLM can request facts, while MCP remains responsible for
        collecting, filtering, grouping, and inspecting logs in deterministic
        code.
        """

        response: dict[str, Any] = await self._make_call(name, arguments)
        self._raise_tool_result_error_if_present(response, name)
        try:
            tool_response: McpGenericToolResponse = McpGenericToolResponse.model_validate(response)
        except ValidationError as exc:
            raise McpClientError(
                self._format_validation_error(
                    f"MCP {name} response did not match expected shape.",
                    exc,
                ),
                mcp_url=self.base_url,
                tool_name=name,
            ) from exc
        if tool_response.error is not None:
            raise McpClientError(
                f"MCP {name} error: {tool_response.error.message}",
                mcp_url=self.base_url,
                tool_name=name,
            )
        if tool_response.result is None:
            raise McpClientError(
                f"MCP {name} response did not include a result object.",
                mcp_url=self.base_url,
                tool_name=name,
            )
        logger.info(
            "MCP workflow tool call completed",
            extra={
                "event": "mcp_tool_call_done",
                "tool_name": name,
                "mcp_url": self.base_url,
            },
        )
        return tool_response.result.structured_content

    async def read_resource(self, uri: str) -> str:
        """Read one MCP resource and return its validated text content."""

        response: dict[str, Any] = await self._make_call(
            name=McpToolName.READ_RESOURCE,
            request_payload=self._build_resource_read_payload(uri),
        )
        try:
            resource_response: McpReadResourceResponse = McpReadResourceResponse.model_validate(
                response
            )
        except ValidationError as exc:
            raise McpClientError(
                self._format_validation_error(
                    "MCP resource response did not match expected shape.",
                    exc,
                ),
                mcp_url=self.base_url,
                tool_name=McpToolName.READ_RESOURCE,
            ) from exc
        if resource_response.error is not None:
            raise McpClientError(
                f"MCP resource read error: {resource_response.error.message}",
                mcp_url=self.base_url,
                tool_name=McpToolName.READ_RESOURCE,
            )
        if resource_response.result is None:
            raise McpClientError(
                "MCP resource response did not include a result object.",
                mcp_url=self.base_url,
                tool_name=McpToolName.READ_RESOURCE,
            )
        if not resource_response.result.contents:
            raise McpClientError(
                "MCP resource response did not include resource contents.",
                mcp_url=self.base_url,
                tool_name=McpToolName.READ_RESOURCE,
            )
        return resource_response.result.contents[0].text

    async def _make_call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        request_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if request_payload is None:
            request_payload = self._build_tool_call_payload(name, arguments)

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
                workflow_jwt = await self._get_workflow_jwt(client, name)
                response = await client.post(
                    self.base_url,
                    json=request_payload,
                    headers=self._build_headers(workflow_jwt),
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
            raise McpClientError(
                f"MCP workflow call failed: {format_exception_chain(exc)}",
                mcp_url=self.base_url,
                tool_name=name,
                hint=(
                    "Check MCP_URL and whether the MCP server is running. "
                    "For Docker Compose commands, remember that localhost means the "
                    "monitoring container, not your host."
                ),
            ) from exc
        except ValueError as exc:
            logger.warning(
                "MCP workflow response was invalid JSON",
                extra={
                    "event": "mcp_tool_call_invalid_json",
                    "tool_name": name,
                    "mcp_url": self.base_url,
                },
            )
            raise McpClientError(
                "MCP workflow response was not valid JSON.",
                mcp_url=self.base_url,
                tool_name=name,
            ) from exc

        if not isinstance(response_payload, dict):
            raise McpClientError(
                "MCP workflow response must be a JSON object.",
                mcp_url=self.base_url,
                tool_name=name,
            )
        return response_payload

    def _raise_tool_result_error_if_present(
        self,
        response: dict[str, Any],
        tool_name: str,
    ) -> None:
        result: object = response.get("result")
        if not isinstance(result, dict) or result.get("isError") is not True:
            return

        structured_content: object = result.get("structuredContent")
        if isinstance(structured_content, dict):
            try:
                tool_error: McpToolResultError = McpToolResultError.model_validate(
                    structured_content
                )
            except ValidationError:
                tool_error = McpToolResultError(
                    status="error",
                    message=str(structured_content.get("message") or "Unknown MCP tool error."),
                )
            retry_tips: str = ""
            if tool_error.retry_tips:
                retry_tips = " Retry tips: " + " ".join(tool_error.retry_tips)
            raise McpClientError(
                f"MCP {tool_name} error: {tool_error.message}.{retry_tips}",
                mcp_url=self.base_url,
                tool_name=tool_name,
            )

        content: object = result.get("content")
        if isinstance(content, list):
            text_items: list[str] = [
                str(item["text"])
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            if text_items:
                raise McpClientError(
                    f"MCP {tool_name} error: {' '.join(text_items)}",
                    mcp_url=self.base_url,
                    tool_name=tool_name,
                )

        raise McpClientError(
            f"MCP {tool_name} returned an error result without a readable message.",
            mcp_url=self.base_url,
            tool_name=tool_name,
        )

    @staticmethod
    def _format_validation_error(message: str, exc: ValidationError) -> str:
        invalid_fields: list[str] = []
        for error in exc.errors():
            location: tuple[object, ...] = error.get("loc", ())
            field_path: str = ".".join(str(part) for part in location) or "<root>"
            invalid_fields.append(f"{field_path}: {error.get('msg', 'invalid value')}")

        if not invalid_fields:
            return message

        field_summary: str = "; ".join(invalid_fields[:8])
        if len(invalid_fields) > 8:
            field_summary = f"{field_summary}; ... and {len(invalid_fields) - 8} more"
        return f"{message} Invalid fields: {field_summary}."

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

    @staticmethod
    def _build_resource_read_payload(uri: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": f"agent-monitoring-{uuid4()}",
            "method": McpToolName.READ_RESOURCE,
            "params": {
                "uri": uri,
            },
        }

    def _has_keycloak_auth(self) -> bool:
        return bool(self.keycloak_url and self.keycloak_client_id and self.keycloak_client_secret)

    def _has_partial_keycloak_auth(self) -> bool:
        return bool(self.keycloak_url or self.keycloak_client_id or self.keycloak_client_secret)

    def _raise_if_auth_missing(self, tool_name: str) -> None:
        if self.workflow_jwt or self._has_keycloak_auth():
            return
        if self._has_partial_keycloak_auth():
            missing = self._missing_keycloak_settings()
            raise McpClientError(
                "MCP Keycloak auth is partially configured. Missing: " + ", ".join(missing),
                mcp_url=self.base_url,
                tool_name=tool_name,
            )
        raise McpClientError(
            "MCP_WORKFLOW_JWT or complete MCP Keycloak client credentials are required "
            "to call the MCP workflow endpoint.",
            mcp_url=self.base_url,
            tool_name=tool_name,
        )

    async def _get_workflow_jwt(self, client: httpx.AsyncClient, tool_name: str) -> str:
        if self._has_keycloak_auth():
            return await self._get_keycloak_workflow_jwt(client, tool_name)
        if self.workflow_jwt:
            return self.workflow_jwt
        self._raise_if_auth_missing(tool_name)
        raise McpClientError(
            "MCP auth configuration did not produce a workflow JWT.",
            mcp_url=self.base_url,
            tool_name=tool_name,
        )

    async def _get_keycloak_workflow_jwt(
        self,
        client: httpx.AsyncClient,
        tool_name: str,
    ) -> str:
        now = time.monotonic()
        if self._cached_workflow_jwt and now < self._cached_workflow_jwt_expires_at:
            return self._cached_workflow_jwt

        try:
            response = await client.post(
                self._keycloak_token_url(),
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.keycloak_client_id,
                    "client_secret": self.keycloak_client_secret,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.HTTPError as exc:
            raise McpClientError(
                f"MCP Keycloak token request failed: {exc}",
                mcp_url=self.base_url,
                tool_name=tool_name,
                hint=(
                    "Check MCP_KEYCLOAK_URL, MCP_KEYCLOAK_CLIENT_ID, "
                    "and MCP_KEYCLOAK_CLIENT_SECRET."
                ),
            ) from exc
        except ValueError as exc:
            raise McpClientError(
                "MCP Keycloak token response was not valid JSON.",
                mcp_url=self.base_url,
                tool_name=tool_name,
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise McpClientError(
                "MCP Keycloak token response did not include an access_token.",
                mcp_url=self.base_url,
                tool_name=tool_name,
            )

        expires_in = payload.get("expires_in")
        ttl_seconds = expires_in if isinstance(expires_in, int) and expires_in > 0 else 300
        self._cached_workflow_jwt = payload["access_token"]
        self._cached_workflow_jwt_expires_at = now + max(
            0, ttl_seconds - self.token_refresh_margin_seconds
        )
        return self._cached_workflow_jwt

    def _missing_keycloak_settings(self) -> list[str]:
        values = {
            "MCP_KEYCLOAK_URL": self.keycloak_url,
            "MCP_KEYCLOAK_CLIENT_ID": self.keycloak_client_id,
            "MCP_KEYCLOAK_CLIENT_SECRET": self.keycloak_client_secret,
        }
        return [name for name, value in values.items() if not value]

    def _keycloak_token_url(self) -> str:
        return f"{self.keycloak_url}/protocol/openid-connect/token"

    @staticmethod
    def _build_headers(workflow_jwt: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {workflow_jwt}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
