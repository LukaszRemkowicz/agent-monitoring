from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class McpToolError(BaseModel):
    """JSON-RPC error body returned by the MCP HTTP endpoint.

    This model exists so the MCP client can validate transport-level failures
    separately from tool payload validation. The error object is part of the
    JSON-RPC envelope, not the monitoring workflow domain data.
    """

    code: int | None = None
    message: str = ""
    data: Any = None


class WorkflowSkill(BaseModel):
    """One reusable skill advertised by the MCP workflow bundle.

    MCP returns skill identifiers as `skill_name`; application code uses
    `.name`. Keeping this alias here makes the external contract explicit while
    preserving a simple Python attribute for service and agent code.
    """

    name: str = Field(alias="skill_name")
    resource_uri: str
    description: str = ""

    model_config = ConfigDict(populate_by_name=True)


class WorkflowTool(BaseModel):
    """One deterministic MCP tool advertised by the workflow bundle.

    The workflow bundle tells the monitoring agent which deterministic tools it
    can call before asking an LLM to summarize or interpret results. This model
    validates that contract before the agent starts using the advertised tool
    list.
    """

    tool_name: str
    description: str = ""
    arguments: list[dict[str, object]] = Field(default_factory=list)


class WorkflowBootstrap(BaseModel):
    """Domain-level workflow bundle used by the monitoring agent.

    This is the application-facing schema for `analyze_daily_log_bundle`.
    `StructuredContent` validates the MCP wire payload first; this model is the
    clean object returned to services and agents.
    """

    workflow_name: str
    prompt: str
    mandatory_skills: list[WorkflowSkill]
    optional_skills: list[WorkflowSkill]
    tools: list[WorkflowTool]


class StructuredContent(BaseModel):
    """Validated `result.structuredContent` for `analyze_daily_log_bundle`.

    MCP tool calls are JSON-RPC responses whose useful payload lives inside
    `result.structuredContent`. This model exists so `McpWorkflowClient.call_tool`
    returns that structured payload as a real Pydantic contract instead of a raw
    dictionary or the full JSON-RPC response envelope.
    """

    workflow_name: str
    prompt: str
    mandatory_skills: list[WorkflowSkill] = Field(default_factory=list)
    optional_skills: list[WorkflowSkill] = Field(default_factory=list)
    tools: list[WorkflowTool] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class McpToolResult(BaseModel):
    """JSON-RPC `result` wrapper for workflow-bundle tool calls.

    The wrapper exists only because MCP nests the validated tool payload under
    `result.structuredContent`. It keeps transport shape validation in one
    place while `call_tool()` still returns only `StructuredContent`.
    """

    structured_content: StructuredContent = Field(alias="structuredContent")

    model_config = ConfigDict(populate_by_name=True)


class McpToolResponse(BaseModel):
    """JSON-RPC response envelope for workflow-bundle tool calls.

    This model validates the outer MCP response before the client extracts
    `StructuredContent`. It intentionally represents the transport envelope, not
    the object returned to higher-level services.
    """

    result: McpToolResult | None = None
    error: McpToolError | None = None


class McpServiceStatus(BaseModel):
    """Structured payload returned by MCP `get_mcp_service_status`.

    Status checks have a different payload shape than the workflow bundle, so
    they use a separate model instead of weakening `StructuredContent` with
    optional fields.
    """

    name: str
    status: str
    environment: str = ""
    client_type: str = ""


class McpServiceStatusResult(BaseModel):
    """JSON-RPC `result` wrapper for MCP service status checks.

    This mirrors `McpToolResult`, but points at the status payload schema. The
    split keeps each MCP tool contract strict even though the JSON-RPC envelope
    shape is similar.
    """

    structured_content: McpServiceStatus = Field(alias="structuredContent")

    model_config = ConfigDict(populate_by_name=True)


class McpServiceStatusResponse(BaseModel):
    """JSON-RPC response envelope for MCP service status checks.

    The client uses this model to validate status responses independently from
    workflow-bundle responses, because both tools return different
    `structuredContent` contracts.
    """

    result: McpServiceStatusResult | None = None
    error: McpToolError | None = None


class LogAnalysisWorkflowResult(BaseModel):
    """Service-level result returned by the log-analysis workflow preparation.

    This schema is not an MCP transport model. It is the object returned from the
    application service after the agent has loaded and validated the workflow
    bundle.
    """

    workflow: WorkflowBootstrap
