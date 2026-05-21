from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

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


class McpToolResultError(BaseModel):
    """Agent-facing error returned inside an MCP tool result.

    FastMCP can return a successful HTTP/JSON-RPC envelope while marking one
    tool execution as `isError=true`. Those errors are domain errors from the
    deterministic tool, not transport failures. This model lets the client
    surface the MCP message and retry tips before validating the successful
    payload shape.
    """

    status: Literal["error"]
    error_code: str = ""
    message: str
    retry_tips: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


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


class WorkflowSkillContent(BaseModel):
    """Mandatory workflow skill text injected into the LLM system prompt.

    MCP intentionally returns only skill metadata in `analyze_daily_log_bundle`
    so the first workflow call stays token-efficient. The monitoring app reads
    mandatory `skill://workflow/...` resources before the first LLM request and
    stores that fetched text in this model. Optional skills remain metadata
    until a later agent loop asks for them.
    """

    name: str
    resource_uri: str
    description: str
    content: str


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


class CollectedLogSource(BaseModel):
    """One source entry returned by MCP `collect_logs`.

    This mirrors the MCP-side source payload closely because the monitoring app
    needs the deterministic collection facts before any LLM call: which sources
    were collected, what failed, and where follow-up MCP snapshot tools should
    look.
    """

    source_key: str
    source_type: Literal["docker", "file"]
    target: str
    description: str
    stream: Literal["stdout", "stderr"] | None = None
    status: Literal["collected", "unavailable"]
    line_count: int
    byte_count: int
    output_file: str | None = None
    error: str | None = None
    retry_tips: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ProjectCollectLogsArtifact(BaseModel):
    """One project artifact returned inside MCP `collect_logs`.

    The monitoring app uses this object as the deterministic source inventory
    for the prepared LLM prompt. It intentionally stores snapshot metadata, not
    raw log content.
    """

    requested_project_name: str
    project_name: str
    workspace: Literal["workflow", "session"]
    snapshot_dir: str
    requested_source_keys: list[str] = Field(default_factory=list)
    requested_since: str | None = None
    requested_until: str | None = None
    warnings: list[str] = Field(default_factory=list)
    retry_tips: list[str] = Field(default_factory=list)
    unknown_requested_source_keys: list[str] = Field(default_factory=list)
    resolved_source_keys: list[str] = Field(default_factory=list)
    collected_at: str
    sources: list[CollectedLogSource] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class CollectLogsArtifact(BaseModel):
    """Structured payload returned by MCP `collect_logs`.

    This is the first deterministic artifact in the log-analysis agent flow.
    It is persisted on `LogAnalysis.mcp_artifact` and summarized into the
    prepared prompt before the app makes any LLM request.
    """

    action: Literal["collect_logs"]
    workspace: Literal["workflow", "session"]
    session_id: str | None = None
    requested_project_names: list[str] = Field(default_factory=list)
    next_step_tips: list[str] = Field(default_factory=list)
    projects: list[ProjectCollectLogsArtifact]

    model_config = ConfigDict(extra="forbid")


class McpCollectLogsResult(BaseModel):
    """JSON-RPC `result` wrapper for MCP `collect_logs` responses."""

    structured_content: CollectLogsArtifact = Field(alias="structuredContent")

    model_config = ConfigDict(populate_by_name=True)


class McpCollectLogsResponse(BaseModel):
    """JSON-RPC response envelope for MCP `collect_logs` responses."""

    result: McpCollectLogsResult | None = None
    error: McpToolError | None = None


class ProjectManifestSummary(BaseModel):
    """Lightweight project summary returned by MCP `list_projects`.

    The log-analysis agent uses this discovery result to know which projects
    the authenticated MCP caller is allowed to inspect before it prepares the
    future LLM request.
    """

    project_name: str
    project_summary: str
    source_keys: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ProjectManifestListPayload(BaseModel):
    """Validated `structuredContent` body returned by MCP `list_projects`."""

    result: list[ProjectManifestSummary] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class McpProjectManifestListResult(BaseModel):
    """JSON-RPC `result` wrapper for MCP `list_projects` responses."""

    structured_content: ProjectManifestListPayload = Field(alias="structuredContent")

    model_config = ConfigDict(populate_by_name=True)


class McpProjectManifestListResponse(BaseModel):
    """JSON-RPC response envelope for MCP `list_projects` responses."""

    result: McpProjectManifestListResult | None = None
    error: McpToolError | None = None


class McpResourceContent(BaseModel):
    """One content item returned by MCP `resources/read`.

    Workflow skill content is transported through MCP resources rather than
    through tool `structuredContent`. This model keeps the resource-read
    response strict while allowing the agent layer to consume a plain skill
    string after validation.
    """

    uri: str
    mime_type: str = Field(alias="mimeType")
    text: str

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class McpReadResourceResult(BaseModel):
    """JSON-RPC `result` wrapper for MCP resource reads."""

    contents: list[McpResourceContent]

    model_config = ConfigDict(extra="forbid")


class McpReadResourceResponse(BaseModel):
    """JSON-RPC response envelope for MCP `resources/read` responses."""

    result: McpReadResourceResult | None = None
    error: McpToolError | None = None


class LogCollectionWindow(BaseModel):
    """Date window prepared by the command/service layer for MCP log collection.

    The agent should not decide how an analysis date maps to MCP timestamps.
    Typer resolves the user-facing date, the service formats the MCP strings,
    and the agent only consumes this already-prepared window.
    """

    since: str
    until: str
    since_datetime: datetime
    until_datetime: datetime


class SnapshotAccessGuidance(BaseModel):
    """Explicit follow-up guidance for persisted MCP log snapshots.

    MCP currently injects a `session_id` into every `collect_logs` call at the
    middleware boundary, including workflow collections. This object makes the
    intended contract clear to the LLM: workflow snapshots are addressed by
    project name, while session ids are only meaningful for session workspaces.
    """

    workspace: Literal["workflow", "session"]
    session_id: str | None = None
    session_id_is_for_session_workspace_only: bool
    workflow_followup_arguments: list[str]
    instruction: str


class LogAnalysisPromptContext(BaseModel):
    """Structured request context for the future log-analysis LLM call.

    This is the typed source of truth for what the LLM will receive after MCP
    collection. It keeps deterministic facts as JSON data, so later LLM
    provider code can send a structured request without parsing prose.
    """

    analysis_date: date
    workflow_name: str
    current_phase: Literal["inspect_collected_logs", "final_report"]
    completed_steps: list[str]
    allowed_actions: list[Literal["call_tools", "final_report"]]
    next_required_action: Literal["call_tools", "final_report"]
    final_report_allowed: bool
    available_projects: list[ProjectManifestSummary] = Field(default_factory=list)
    mandatory_skills: list[WorkflowSkillContent]
    optional_skills: list[WorkflowSkill] = Field(default_factory=list)
    collection: CollectLogsArtifact
    snapshot_access: SnapshotAccessGuidance
    available_tools: list[WorkflowTool] = Field(default_factory=list)
    report_contract: dict[str, str]
    instructions: list[str] = Field(default_factory=list)


class LogAnalysisPreparedPrompt(BaseModel):
    """Prompt material prepared for the future LLM log-analysis call.

    The app stops at this object for the current implementation. It lets us
    inspect exactly what would be sent to the LLM after deterministic MCP
    collection, without performing the LLM request yet. The user prompt is a
    JSON serialization of `context`, not hand-built markdown.
    """

    system_prompt: str
    context: LogAnalysisPromptContext

    @property
    def user_prompt(self) -> str:
        """Return the structured LLM request context as pretty JSON."""

        return self.context.model_dump_json(indent=2)


class LogAnalysisFinalReport(BaseModel):
    """Validated final JSON report returned by the log-analysis LLM call.

    Phase 2A intentionally stops at a single LLM request, so this is the
    boundary contract between free-form model output and persisted monitoring
    state. The LLM may reason from the deterministic MCP artifact, but only this
    validated report shape is allowed to update the database summary fields.
    """

    action: Literal["final_report"]
    summary: str
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    key_findings: list[str]
    recommendations: str
    trend_summary: str

    model_config = ConfigDict(extra="forbid")


class LogAnalysisAgentContext(BaseModel):
    """Agent context assembled before the first log-analysis LLM call."""

    workflow: WorkflowBootstrap
    collect_logs: CollectLogsArtifact
    prompt: LogAnalysisPreparedPrompt
    final_report: LogAnalysisFinalReport
    log_window_since: datetime
    log_window_until: datetime
    llm_tokens_used: int = 0
    llm_cost_usd: float = 0.0
    llm_report_execution_time_seconds: float = 0.0


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
    application service after the agent has loaded the workflow bundle,
    collected logs through MCP, and prepared the prompt for the future LLM call.
    """

    analysis: LogAnalysisOut
    agent_context: LogAnalysisAgentContext

    @property
    def workflow(self) -> WorkflowBootstrap:
        return self.agent_context.workflow

    @property
    def collect_logs(self) -> CollectLogsArtifact:
        return self.agent_context.collect_logs

    @property
    def prepared_prompt(self) -> LogAnalysisPreparedPrompt:
        return self.agent_context.prompt


class SitemapAnalysisWorkflowResult(BaseModel):
    """Service-level result returned by sitemap-analysis workflow preparation."""

    analysis: SitemapAnalysisOut
