from __future__ import annotations

from datetime import date, datetime
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


class LogAnalysisIn(BaseModel):
    """Validated data passed from services into the log-analysis repository."""

    analysis_date: date
    mcp_artifact: dict[str, Any] = Field(default_factory=dict)
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_stage: str | None = None
    log_window_since: datetime | None = None
    log_window_until: datetime | None = None
    mcp_collect_logs_id: str | None = None
    summary: str
    severity: str = "INFO"
    key_findings: list[str] = Field(default_factory=list)
    recommendations: str = ""
    trend_summary: str = ""
    execution_time_seconds: float = 0.0
    gpt_tokens_used: int = 0
    gpt_cost_usd: float = 0.0
    email_sent: bool = False
    error_message: str = ""


class LogAnalysisOut(LogAnalysisIn):
    """Validated log-analysis data returned by the repository layer."""

    id: int
    created_at: datetime

    @classmethod
    def from_model(cls, analysis: Any) -> LogAnalysisOut:
        return cls.model_validate(
            {
                "id": analysis.id,
                "created_at": analysis.created_at,
                "analysis_date": analysis.analysis_date,
                "mcp_artifact": analysis.mcp_artifact,
                "status": analysis.status,
                "started_at": analysis.started_at,
                "finished_at": analysis.finished_at,
                "failure_stage": analysis.failure_stage,
                "log_window_since": analysis.log_window_since,
                "log_window_until": analysis.log_window_until,
                "mcp_collect_logs_id": analysis.mcp_collect_logs_id,
                "summary": analysis.summary,
                "severity": analysis.severity,
                "key_findings": analysis.key_findings,
                "recommendations": analysis.recommendations,
                "trend_summary": analysis.trend_summary,
                "execution_time_seconds": analysis.execution_time_seconds,
                "gpt_tokens_used": analysis.gpt_tokens_used,
                "gpt_cost_usd": analysis.gpt_cost_usd,
                "email_sent": analysis.email_sent,
                "error_message": analysis.error_message,
            }
        )


class SitemapAnalysisIn(BaseModel):
    """Validated data passed from services into the sitemap-analysis repository."""

    analysis_date: date
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_stage: str | None = None
    fetch_duration_seconds: float = 0.0
    root_sitemap_url: str
    total_sitemaps: int = 0
    total_urls: int = 0
    issue_summary: dict[str, int] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    severity: str = "INFO"
    key_findings: list[str] = Field(default_factory=list)
    recommendations: str = ""
    trend_summary: str = ""
    execution_time_seconds: float = 0.0
    gpt_tokens_used: int = 0
    gpt_cost_usd: float = 0.0
    email_sent: bool = False
    error_message: str = ""


class SitemapAnalysisOut(SitemapAnalysisIn):
    """Validated sitemap-analysis data returned by the repository layer."""

    id: int
    created_at: datetime

    @classmethod
    def from_model(cls, analysis: Any) -> SitemapAnalysisOut:
        return cls.model_validate(
            {
                "id": analysis.id,
                "created_at": analysis.created_at,
                "analysis_date": analysis.analysis_date,
                "status": analysis.status,
                "started_at": analysis.started_at,
                "finished_at": analysis.finished_at,
                "failure_stage": analysis.failure_stage,
                "fetch_duration_seconds": analysis.fetch_duration_seconds,
                "root_sitemap_url": analysis.root_sitemap_url,
                "total_sitemaps": analysis.total_sitemaps,
                "total_urls": analysis.total_urls,
                "issue_summary": analysis.issue_summary,
                "issues": analysis.issues,
                "summary": analysis.summary,
                "severity": analysis.severity,
                "key_findings": analysis.key_findings,
                "recommendations": analysis.recommendations,
                "trend_summary": analysis.trend_summary,
                "execution_time_seconds": analysis.execution_time_seconds,
                "gpt_tokens_used": analysis.gpt_tokens_used,
                "gpt_cost_usd": analysis.gpt_cost_usd,
                "email_sent": analysis.email_sent,
                "error_message": analysis.error_message,
            }
        )


class LogAnalysisWorkflowResult(BaseModel):
    """Service-level result returned by the log-analysis workflow preparation.

    This schema is not an MCP transport model. It is the object returned from the
    application service after the agent has loaded and validated the workflow
    bundle.
    """

    analysis: LogAnalysisOut
    workflow: WorkflowBootstrap


class SitemapAnalysisWorkflowResult(BaseModel):
    """Service-level result returned by sitemap-analysis workflow preparation."""

    analysis: SitemapAnalysisOut
