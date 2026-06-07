from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from utils.byte_size import format_byte_size
from utils.log_artifacts import collect_log_artifact_byte_count


class LogAnalysisSeverity(StrEnum):
    """Allowed persisted/report severity values for log analysis."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class RecommendedAction(StrEnum):
    """Shared recommended next action values for deterministic workflow hints."""

    LLM_MAY_DECIDE = "llm_may_decide"
    CALL_TOOLS = "call_tools"


class LogAnalysisPromptEvidenceKind(StrEnum):
    """Prompt evidence modes prepared before the LLM tool loop starts."""

    HISTORY_COMPARISON = "history_comparison"
    GROUPED_ERROR_BASELINE = "grouped_error_baseline"


class LogAnalysisGroupedErrorEvidenceLabel(StrEnum):
    """Labels for compact grouped-error evidence sides in log-analysis prompts."""

    PREVIOUS = "previous"
    CURRENT = "current"


class LogAnalysisHistoryComparisonStatus(StrEnum):
    """History-comparison availability state for log-analysis prompt evidence."""

    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"


class LogSourceCollectionStatus(StrEnum):
    """Collection status values for one log source in MCP collect_logs output."""

    COLLECTED = "collected"
    UNAVAILABLE = "unavailable"


class LogWorkspace(StrEnum):
    """MCP log snapshot workspace values."""

    WORKFLOW = "workflow"
    SESSION = "session"


class LogAnalysisEvidenceMode(StrEnum):
    """Prompt evidence-mode values used to guide LLM decision behavior."""

    MCP_TOOL_RESULTS_REQUIRED = "mcp_tool_results_required"
    METADATA_AND_PREVIOUS_ANALYSIS_ONLY = "metadata_and_previous_analysis_only"
    SOURCE_COVERAGE_CHANGED_REQUIRES_TOOLS = "source_coverage_changed_requires_tools"
    HISTORY_GUARD_REQUIRES_TOOLS = "history_guard_requires_tools"
    CURRENT_GROUPED_ERRORS_AVAILABLE = "current_grouped_errors_available"
    CURRENT_TOOL_RESULTS_AVAILABLE = "current_tool_results_available"


class LogAnalysisNextRequiredAction(StrEnum):
    """Next action expected from the log-analysis LLM loop."""

    CALL_TOOLS = "call_tools"
    FINAL_REPORT = "final_report"
    CHOOSE_NEXT_ACTION = "choose_next_action"


class LogAnalysisAllowedAction(StrEnum):
    """Allowed top-level actions exposed to the log-analysis LLM."""

    CALL_TOOLS = "call_tools"
    READ_SKILLS = "read_skills"
    FINAL_REPORT = "final_report"


class LogAnalysisPromptPhase(StrEnum):
    """Prompt phase values for the log-analysis LLM context."""

    INSPECT_COLLECTED_LOGS = "inspect_collected_logs"
    FINAL_REPORT = "final_report"


class McpToolName(StrEnum):
    """Shared MCP tool names referenced by deterministic log-analysis code."""

    ANALYZE_DAILY_LOG_BUNDLE = "analyze_daily_log_bundle"
    ANALYZE_SITEMAP_BUNDLE = "analyze_sitemap_bundle"
    COLLECT_LOGS = "collect_logs"
    LIST_PROJECTS = "list_projects"
    READ_RESOURCE = "resources/read"
    GROUP_ERRORS = "group_errors"
    INSPECT_PROXY_ACTIVITY = "inspect_proxy_activity"
    BUILD_INCIDENT_BUNDLE = "build_incident_bundle"
    GREP_LOG_SNAPSHOT = "grep_log_snapshot"


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
    when_useful: str = ""

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
    status: LogSourceCollectionStatus
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
    workspace: LogWorkspace
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

    action: Literal[McpToolName.COLLECT_LOGS]
    workspace: LogWorkspace
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


class McpGenericToolPayload(BaseModel):
    """Validated `structuredContent` body for follow-up MCP tool calls.

    The LLM can choose deterministic follow-up MCP tools from the advertised
    workflow bundle. Those tools can return different domain shapes, so the
    client validates only the shared JSON-RPC wrapper and keeps the tool-specific
    structured content as a dictionary for prompt feedback and persistence.
    """

    structured_content: dict[str, Any] = Field(alias="structuredContent")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class McpGenericToolResponse(BaseModel):
    """JSON-RPC response envelope for generic follow-up MCP tool calls."""

    result: McpGenericToolPayload | None = None
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

    workspace: LogWorkspace
    session_id: str | None = None
    session_id_is_for_session_workspace_only: bool
    workflow_followup_arguments: list[str]
    instruction: str


LogAnalysisCoverageTotals = dict[str, int]


class LogAnalysisCoverageSnapshotSource(BaseModel):
    """Coverage state for one collected log source."""

    source_key: str
    source_type: str = ""
    status: str = ""
    line_count: int = 0
    byte_count: int = 0
    zero_lines: bool = False
    has_output_file: bool = False
    error: str | None = None


class LogAnalysisCoverageSnapshotProject(BaseModel):
    """Coverage state for one collected project."""

    project_name: str
    snapshot_dir: str = ""
    warnings: list[str] = Field(default_factory=list)
    sources: list[LogAnalysisCoverageSnapshotSource] = Field(default_factory=list)


class LogAnalysisCoverageSnapshot(BaseModel):
    """Full source-level coverage snapshot used for deterministic comparison."""

    projects: list[LogAnalysisCoverageSnapshotProject] = Field(default_factory=list)
    totals: LogAnalysisCoverageTotals = Field(default_factory=dict)


class LogAnalysisCompactCoverageSnapshot(BaseModel):
    """Prompt-safe coverage snapshot with source-level details removed."""

    totals: LogAnalysisCoverageTotals = Field(default_factory=dict)


class LogAnalysisSourceCoverageComparison(BaseModel):
    """Small deterministic hint comparing current collection source coverage state to history."""

    available: bool
    source_coverage_changed: bool = False
    changed_sources: list[str] = Field(default_factory=list)
    tool_scope_by_project: dict[str, list[str]] = Field(default_factory=dict)
    recommended_action: RecommendedAction
    rationale: str


class LogAnalysisGroupedErrorSeenLine(BaseModel):
    """Line reference shape returned by MCP `group_errors` for first/last sightings."""

    line: str = ""
    line_number: int = 0
    line_truncated: bool = False
    output_file: str = ""
    source_key: str = ""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_mcp_payload(cls, value: Any) -> LogAnalysisGroupedErrorSeenLine | None:
        """Build a seen-line row from loose MCP group_errors payload data."""

        if not isinstance(value, dict):
            return None
        return cls(
            line=str(value.get("line") or ""),
            line_number=_int_or_zero(value.get("line_number")),
            line_truncated=bool(value.get("line_truncated")),
            output_file=str(value.get("output_file") or ""),
            source_key=str(value.get("source_key") or ""),
        )


class LogAnalysisGroupedErrorSignal(BaseModel):
    """One grouped-error fingerprint from MCP `group_errors` output."""

    fingerprint: str
    project_name: str
    category: str = ""
    severity: str = ""
    count: int = 0
    source_keys: list[str] = Field(default_factory=list)
    request_paths: list[str] = Field(default_factory=list)
    status_codes: list[int] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    message_summary: str = ""
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    first_seen: LogAnalysisGroupedErrorSeenLine | None = None
    last_seen: LogAnalysisGroupedErrorSeenLine | None = None

    @classmethod
    def from_mcp_payload(
        cls,
        value: Any,
        *,
        project_name: str,
    ) -> LogAnalysisGroupedErrorSignal | None:
        """Build one grouped-error signal from a loose MCP group row."""

        if not isinstance(value, dict):
            return None
        fingerprint: str = str(value.get("fingerprint") or "")
        if not fingerprint:
            return None
        return cls(
            fingerprint=fingerprint,
            project_name=project_name,
            category=str(value.get("category") or ""),
            severity=str(value.get("severity") or ""),
            count=_int_or_zero(value.get("count")),
            source_keys=_string_list(value.get("source_keys")),
            request_paths=_string_list(value.get("request_paths")),
            status_codes=_int_list(value.get("status_codes")),
            levels=_string_list(value.get("levels")),
            message_summary=str(value.get("message_summary") or ""),
            first_timestamp=_optional_string(value.get("first_timestamp")),
            last_timestamp=_optional_string(value.get("last_timestamp")),
            first_seen=LogAnalysisGroupedErrorSeenLine.from_mcp_payload(value.get("first_seen")),
            last_seen=LogAnalysisGroupedErrorSeenLine.from_mcp_payload(value.get("last_seen")),
        )


class LogAnalysisGroupedErrorsResult(BaseModel):
    """MCP `group_errors` structured result shape stored for history comparison."""

    action: McpToolName = McpToolName.GROUP_ERRORS
    analysis_cautions: list[str] = Field(default_factory=list)
    grouped_error_count: int = 0
    groups: list[LogAnalysisGroupedErrorSignal] = Field(default_factory=list)
    matching_line_count: int = 0
    max_groups: int = 0
    next_step_tips: list[str] = Field(default_factory=list)
    project_name: str = ""
    requested_project_name: str = ""
    searched_source_keys: list[str] = Field(default_factory=list)
    session_id: str | None = None
    snapshot_dir: str = ""
    summary: str = ""
    truncated: bool = False
    workspace: str = ""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_mcp_payload(cls, structured_content: dict[str, Any]) -> LogAnalysisGroupedErrorsResult:
        """Build the typed stored result from loose MCP group_errors output."""

        project_name: str = str(structured_content.get("project_name") or "")
        groups: object = structured_content.get("groups", [])
        grouped_error_signals: list[LogAnalysisGroupedErrorSignal] = []
        if isinstance(groups, list):
            grouped_error_signals = [
                signal
                for group in groups
                if (
                    signal := LogAnalysisGroupedErrorSignal.from_mcp_payload(
                        group,
                        project_name=project_name,
                    )
                )
                is not None
            ]
        return cls(
            action=McpToolName.GROUP_ERRORS,
            analysis_cautions=_string_list(structured_content.get("analysis_cautions")),
            grouped_error_count=_int_or_zero(structured_content.get("grouped_error_count")),
            groups=grouped_error_signals,
            matching_line_count=_int_or_zero(structured_content.get("matching_line_count")),
            max_groups=_int_or_zero(structured_content.get("max_groups")),
            next_step_tips=_string_list(structured_content.get("next_step_tips")),
            project_name=project_name,
            requested_project_name=str(structured_content.get("requested_project_name") or ""),
            searched_source_keys=_string_list(structured_content.get("searched_source_keys")),
            session_id=_optional_string(structured_content.get("session_id")),
            snapshot_dir=str(structured_content.get("snapshot_dir") or ""),
            summary=str(structured_content.get("summary") or ""),
            truncated=bool(structured_content.get("truncated")),
            workspace=str(structured_content.get("workspace") or ""),
        )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [_int_or_zero(item) for item in value if isinstance(item, int | str)]


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


LogAnalysisFingerprintArgumentValue = (
    str | int | float | bool | None | list[str] | list[int] | list[float] | list[bool]
)


class LogAnalysisFingerprintLogWindow(BaseModel):
    """Log window represented by one fingerprint packet."""

    since: str = ""
    until: str = ""

    model_config = ConfigDict(extra="forbid")


class LogAnalysisFingerprintCollection(BaseModel):
    """Collection facts represented by one fingerprint packet."""

    workspace: str = ""
    requested_project_names: list[str] = Field(default_factory=list)
    project_count: int = 0

    model_config = ConfigDict(extra="forbid")


class LogAnalysisToolResultFingerprint(BaseModel):
    """Stable hash identity for one deterministic tool result."""

    tool_name: str
    arguments_hash: str
    action: str = ""
    result_hash: str

    model_config = ConfigDict(extra="forbid")


class LogAnalysisGroupedErrorRunFingerprint(BaseModel):
    """One stored grouped-error tool run and the fingerprints it returned."""

    arguments: dict[str, LogAnalysisFingerprintArgumentValue] = Field(default_factory=dict)
    result: LogAnalysisGroupedErrorsResult = Field(default_factory=LogAnalysisGroupedErrorsResult)

    model_config = ConfigDict(extra="forbid")


class LogAnalysisReportFingerprint(BaseModel):
    """Compact report-level counters stored with fingerprints."""

    severity: str = ""
    key_finding_count: int = 0
    evidence_count: int = 0
    coverage_gap_count: int = 0
    watch_only_count: int = 0

    model_config = ConfigDict(extra="forbid")


class LogAnalysisGroupedErrorHistorySummary(BaseModel):
    """Prompt-safe summary for grouped-error history stripped from old reports."""

    signal_count: int = 0
    run_count: int = 0
    detail: str = ""

    model_config = ConfigDict(extra="forbid")


class LogAnalysisFingerprints(BaseModel):
    """Typed fingerprint packet stored for one log-analysis run."""

    version: str = ""
    log_window: LogAnalysisFingerprintLogWindow = Field(
        default_factory=LogAnalysisFingerprintLogWindow
    )
    collection: LogAnalysisFingerprintCollection = Field(
        default_factory=LogAnalysisFingerprintCollection
    )
    coverage_totals: LogAnalysisCoverageTotals = Field(default_factory=dict)
    tool_results: list[LogAnalysisToolResultFingerprint] = Field(default_factory=list)
    grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint] = Field(default_factory=list)
    grouped_error_history_summary: LogAnalysisGroupedErrorHistorySummary | None = None
    report: LogAnalysisReportFingerprint = Field(default_factory=LogAnalysisReportFingerprint)

    model_config = ConfigDict(extra="forbid")


class PreviousLogAnalysisContext(BaseModel):
    """Compact previous successful run data included for current-run comparison."""

    analysis_date: date
    summary: str
    severity: LogAnalysisSeverity
    trend_summary: str = ""
    fingerprints: LogAnalysisFingerprints = Field(default_factory=LogAnalysisFingerprints)
    evidence_fingerprints: list[str] = Field(default_factory=list)
    known_patterns: list[dict[str, Any]] = Field(default_factory=list)
    coverage_snapshot: LogAnalysisCoverageSnapshot = Field(
        default_factory=LogAnalysisCoverageSnapshot
    )
    fingerprint_version: str

    @classmethod
    def from_analysis(cls, analysis: Any) -> PreviousLogAnalysisContext:
        return cls.model_validate(
            {
                "analysis_date": analysis.analysis_date,
                "summary": analysis.summary,
                "severity": analysis.severity,
                "trend_summary": analysis.trend_summary,
                "fingerprints": analysis.fingerprints,
                "evidence_fingerprints": analysis.evidence_fingerprints,
                "known_patterns": analysis.known_patterns,
                "coverage_snapshot": analysis.coverage_snapshot,
                "fingerprint_version": analysis.fingerprint_version,
            }
        )


class PreviousLogAnalysisPromptContext(BaseModel):
    """Prompt-safe previous run context with source-level coverage removed."""

    analysis_date: date
    summary: str
    severity: LogAnalysisSeverity
    trend_summary: str = ""
    fingerprints: LogAnalysisFingerprints = Field(default_factory=LogAnalysisFingerprints)
    evidence_fingerprints: list[str] = Field(default_factory=list)
    known_patterns: list[dict[str, Any]] = Field(default_factory=list)
    coverage_snapshot: LogAnalysisCompactCoverageSnapshot = Field(
        default_factory=LogAnalysisCompactCoverageSnapshot
    )
    fingerprint_version: str


class LogAnalysisGroupedErrorComparison(BaseModel):
    """Current grouped-error fingerprints compared with the previous run."""

    available: bool
    current_tool_scope_by_project: dict[str, list[str]] = Field(default_factory=dict)
    previous_group_count: int = 0
    current_group_count: int = 0
    new_fingerprints: list[str] = Field(default_factory=list)
    resolved_fingerprints: list[str] = Field(default_factory=list)
    persisting_fingerprints: list[str] = Field(default_factory=list)
    worsened_fingerprints: list[str] = Field(default_factory=list)
    improved_fingerprints: list[str] = Field(default_factory=list)
    new_high_severity_fingerprints: list[str] = Field(default_factory=list)
    resolved_high_severity_fingerprints: list[str] = Field(default_factory=list)
    resolved_high_severity_tool_scope_by_project: dict[str, list[str]] = Field(default_factory=dict)
    resolved_high_severity_current_scope_covered: bool = True
    current_changed_groups: list[LogAnalysisGroupedErrorSignal] = Field(default_factory=list)
    previous_changed_groups: list[LogAnalysisGroupedErrorSignal] = Field(default_factory=list)
    rationale: str


class LogAnalysisPromptGroupedErrorExample(BaseModel):
    """Small example from a grouped-error delta for prompt grounding."""

    fingerprint: str
    project_name: str = ""
    category: str = ""
    severity: str = ""
    count: int = 0
    source_keys: list[str] = Field(default_factory=list)
    request_paths: list[str] = Field(default_factory=list)
    status_codes: list[int] = Field(default_factory=list)
    message_summary: str = ""


class LogAnalysisPromptGroupedErrorFingerprint(BaseModel):
    """Thin grouped-error fingerprint row for previous/current prompt comparison."""

    fingerprint: str
    project_name: str = ""
    category: str = ""
    severity: str = ""
    source_keys: list[str] = Field(default_factory=list)
    status_codes: list[int] = Field(default_factory=list)


class LogAnalysisPromptGroupedErrorComparison(BaseModel):
    """Prompt-safe grouped-error comparison view.

    Full fingerprint lists stay in deterministic Python state. The LLM receives
    counts plus a bounded set of examples so compare-history stays cheap.
    """

    available: bool
    current_tool_scope_by_project: dict[str, list[str]] = Field(default_factory=dict)
    previous_group_count: int = 0
    current_group_count: int = 0
    new_fingerprint_count: int = 0
    resolved_fingerprint_count: int = 0
    persisting_fingerprint_count: int = 0
    worsened_fingerprint_count: int = 0
    improved_fingerprint_count: int = 0
    new_high_severity_fingerprint_count: int = 0
    new_high_severity_fingerprints: list[str] = Field(default_factory=list)
    resolved_high_severity_fingerprint_count: int = 0
    resolved_high_severity_fingerprints: list[str] = Field(default_factory=list)
    resolved_high_severity_tool_scope_by_project: dict[str, list[str]] = Field(default_factory=dict)
    resolved_high_severity_current_scope_covered: bool = True
    evidence_quality_warnings: list[str] = Field(default_factory=list)
    next_evidence_hint: str = ""
    priority_current_examples: list[LogAnalysisPromptGroupedErrorExample] = Field(
        default_factory=list,
        description=(
            "Current changed examples ordered for report wording: new high-severity "
            "families first, then other changed families."
        ),
    )
    current_changed_examples: list[LogAnalysisPromptGroupedErrorExample] = Field(
        default_factory=list
    )
    previous_changed_examples: list[LogAnalysisPromptGroupedErrorExample] = Field(
        default_factory=list
    )
    rationale: str


class LogAnalysisPromptGroupedErrorEvidence(BaseModel):
    """Prompt-safe grouped-error evidence for one side of history context.

    This is not a diff. It gives the LLM compact previous/current grouped-error
    evidence without raw log lines.
    """

    available: bool = False
    label: LogAnalysisGroupedErrorEvidenceLabel
    tool_scope_by_project: dict[str, list[str]] = Field(default_factory=dict)
    run_count: int = 0
    group_count: int = 0
    severity_counts: dict[str, int] = Field(default_factory=dict)
    category_counts: dict[str, int] = Field(default_factory=dict)
    status_code_counts: dict[str, int] = Field(default_factory=dict)
    source_key_counts: dict[str, int] = Field(default_factory=dict)
    fingerprints: list[LogAnalysisPromptGroupedErrorFingerprint] = Field(default_factory=list)
    rationale: str = ""


class LogAnalysisCurrentCoverage(BaseModel):
    """Current-run collection source coverage state facts for report coverage gaps."""

    zero_line_sources: list[str] = Field(default_factory=list)
    unavailable_sources: list[str] = Field(default_factory=list)


class LogAnalysisPromptCollectedSource(BaseModel):
    """Compact source collection facts needed by the LLM prompt."""

    source_key: str
    status: LogSourceCollectionStatus
    line_count: int
    zero_lines: bool


class LogAnalysisPromptCollectedProject(BaseModel):
    """Compact project collection facts needed by the LLM prompt."""

    project_name: str
    snapshot_dir: str
    resolved_source_keys: list[str] = Field(default_factory=list)
    sources: list[LogAnalysisPromptCollectedSource] = Field(default_factory=list)


class LogAnalysisPromptCollection(BaseModel):
    """Compact collect_logs prompt view; full artifact stays in Python/storage."""

    action: Literal[McpToolName.COLLECT_LOGS]
    workspace: LogWorkspace
    session_id: str | None = None
    projects: list[LogAnalysisPromptCollectedProject]


class LogAnalysisPromptHistoryComparison(BaseModel):
    """Single prompt object describing deterministic history comparison state."""

    status: LogAnalysisHistoryComparisonStatus = LogAnalysisHistoryComparisonStatus.DISABLED
    decision_prompt: dict[str, Any] = Field(default_factory=dict)
    source_coverage: LogAnalysisSourceCoverageComparison | None = None
    previous_grouped_errors: LogAnalysisPromptGroupedErrorEvidence | None = None
    current_grouped_errors: LogAnalysisPromptGroupedErrorEvidence | None = None
    grouped_error_diff: LogAnalysisPromptGroupedErrorComparison | None = None


class LogAnalysisPromptHistoryBaseline(BaseModel):
    """Prompt object for no-compare-history baseline review.

    This is intentionally not named comparison: deterministic previous/current
    comparison is disabled in this mode. The LLM receives prior baseline facts
    and current baseline facts, then chooses whether that is enough.
    """

    mode: Literal["no_compare_history"] = "no_compare_history"
    decision_prompt: dict[str, Any] = Field(default_factory=dict)
    previous_grouped_errors: LogAnalysisPromptGroupedErrorEvidence | None = None
    current_grouped_errors: LogAnalysisPromptGroupedErrorEvidence | None = None


class LogAnalysisPromptHistoryComparisonState(BaseModel):
    """Compact history-comparison state included in prompt evidence."""

    status: LogAnalysisHistoryComparisonStatus


class LogAnalysisPromptCompactedEvidence(BaseModel):
    """Compact deterministic comparison payload included in prompt evidence."""

    source_coverage: LogAnalysisSourceCoverageComparison | None = None
    grouped_error_diff: LogAnalysisPromptGroupedErrorComparison | None = None


class LogAnalysisPromptEvidence(BaseModel):
    """Single typed prompt evidence object prepared before the LLM loop."""

    kind: LogAnalysisPromptEvidenceKind
    decision_prompt: dict[str, Any] = Field(default_factory=dict)
    history_comparison: LogAnalysisPromptHistoryComparisonState | None = None
    prompt_compacted: LogAnalysisPromptCompactedEvidence | None = None
    previous_grouped_errors: LogAnalysisPromptGroupedErrorEvidence | None = None
    current_grouped_errors: LogAnalysisPromptGroupedErrorEvidence | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        """Return the exact JSON shape expected by the LLM prompt."""

        data: dict[str, Any] = {
            "kind": self.kind.value,
            "decision_prompt": self.decision_prompt,
        }
        if self.kind == LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON:
            data["history_comparison"] = (
                self.history_comparison.model_dump(mode="json")
                if self.history_comparison is not None
                else None
            )
            data["prompt_compacted"] = (
                self.prompt_compacted.model_dump(mode="json")
                if self.prompt_compacted is not None
                else None
            )
            return data
        data["previous_grouped_errors"] = (
            self.previous_grouped_errors.model_dump(mode="json")
            if self.previous_grouped_errors is not None
            else None
        )
        data["current_grouped_errors"] = (
            self.current_grouped_errors.model_dump(mode="json")
            if self.current_grouped_errors is not None
            else None
        )
        return data


class LogAnalysisPromptContext(BaseModel):
    """Structured request context for the future log-analysis LLM call.

    This is the typed source of truth for what the LLM will receive after MCP
    collection. It keeps deterministic facts as JSON data, so later LLM
    provider code can send a structured request without parsing prose.
    """

    analysis_date: date
    workflow_name: str
    current_phase: LogAnalysisPromptPhase
    completed_steps: list[str]
    historical_context_available: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    previous_analysis: PreviousLogAnalysisPromptContext | None = None
    current_coverage: LogAnalysisCurrentCoverage
    evidence_mode: LogAnalysisEvidenceMode
    current_tool_result_count: int = 0
    trend_summary_instruction: str = ""
    allowed_actions: list[LogAnalysisAllowedAction]
    next_required_action: LogAnalysisNextRequiredAction
    final_report_allowed: bool
    available_projects: list[ProjectManifestSummary] = Field(default_factory=list)
    mandatory_skills: list[WorkflowSkill]
    optional_skills: list[WorkflowSkill] = Field(default_factory=list)
    collection: LogAnalysisPromptCollection
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

        return self.context.model_dump_json()


class LogAnalysisFinalReport(BaseModel):
    """Validated final JSON report returned by the log-analysis LLM call.

    This is the boundary contract between free-form model output and persisted
    monitoring state. The LLM may reason from the deterministic MCP artifact and
    follow-up MCP tool results, but only this validated report shape is allowed
    to update the database summary fields.
    """

    action: Literal["final_report"]
    summary: str
    severity: LogAnalysisSeverity
    severity_rationale: str
    key_findings: list[str]
    evidence: list[str]
    coverage_gaps: list[str]
    recommendations: str
    watch_only_items: list[str]
    trend_summary: str

    model_config = ConfigDict(extra="forbid")


class LogAnalysisToolCall(BaseModel):
    """One deterministic MCP tool call requested by the LLM action loop."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class LogAnalysisToolCallRequest(BaseModel):
    """LLM action requesting deterministic MCP follow-up tool calls."""

    action: Literal["call_tools"]
    tool_calls: list[LogAnalysisToolCall]

    model_config = ConfigDict(extra="forbid")


class LogAnalysisSkillReadRequest(BaseModel):
    """LLM action requesting optional MCP workflow skill resources."""

    action: Literal["read_skills"]
    skill_names: list[str]

    model_config = ConfigDict(extra="forbid")


class LogAnalysisToolResult(BaseModel):
    """Structured result returned from one deterministic MCP follow-up tool."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    structured_content: dict[str, Any]


class LogAnalysisAgentContext(BaseModel):
    """Agent context assembled before the first log-analysis LLM call."""

    workflow: WorkflowBootstrap
    collect_logs: CollectLogsArtifact
    prompt: LogAnalysisPreparedPrompt
    tool_results: list[LogAnalysisToolResult] = Field(default_factory=list)
    final_report: LogAnalysisFinalReport
    log_window_since: datetime
    log_window_until: datetime
    llm_tokens_used: int = 0
    llm_cost_usd: float = 0.0
    llm_report_execution_time_seconds: float = 0.0


class LogAnalysisFingerprintPacket(BaseModel):
    """Compact structured comparison data derived from one log-analysis run."""

    fingerprint_version: str
    fingerprints: LogAnalysisFingerprints = Field(default_factory=LogAnalysisFingerprints)
    evidence_fingerprints: list[str] = Field(default_factory=list)
    known_patterns: list[dict[str, Any]] = Field(default_factory=list)
    coverage_snapshot: dict[str, Any] = Field(default_factory=dict)


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
    fingerprints: LogAnalysisFingerprints = Field(default_factory=LogAnalysisFingerprints)
    evidence_fingerprints: list[str] = Field(default_factory=list)
    known_patterns: list[dict[str, Any]] = Field(default_factory=list)
    coverage_snapshot: dict[str, Any] = Field(default_factory=dict)
    fingerprint_version: str = ""
    execution_time_seconds: float = 0.0
    gpt_tokens_used: int = 0
    gpt_cost_usd: float = 0.0
    email_sent: bool = False
    error_message: str = ""

    @property
    def log_size(self) -> str:
        return format_byte_size(collect_log_artifact_byte_count(self.mcp_artifact))


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
                "fingerprints": analysis.fingerprints,
                "evidence_fingerprints": analysis.evidence_fingerprints,
                "known_patterns": analysis.known_patterns,
                "coverage_snapshot": analysis.coverage_snapshot,
                "fingerprint_version": analysis.fingerprint_version,
                "execution_time_seconds": analysis.execution_time_seconds,
                "gpt_tokens_used": analysis.gpt_tokens_used,
                "gpt_cost_usd": analysis.gpt_cost_usd,
                "email_sent": analysis.email_sent,
                "error_message": analysis.error_message,
            }
        )


class LogAnalysisLLMCallIn(BaseModel):
    """Validated LLM/tool-loop step passed into the repository layer."""

    trace_id: str = ""
    analysis_date: date | None = None
    workflow_name: str | None = None
    mcp_session_id: str | None = None
    iteration: int | None = None
    step_type: str
    action: str | None = None
    tool_name: str | None = None
    skill_name: str | None = None
    requested_tool_names_text: str = ""
    requested_skill_names_text: str = ""
    arguments_hash: str | None = None
    arguments_text: str = ""
    status: str | None = None
    duplicate_skipped: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    llm_response_text: str = ""
    error_message: str = ""
    result_summary: str = ""


class LogAnalysisLLMCallOut(LogAnalysisLLMCallIn):
    """Validated LLM/tool-loop step returned by repositories."""

    id: int
    created_at: datetime

    @classmethod
    def from_model(cls, step: Any) -> LogAnalysisLLMCallOut:
        return cls.model_validate(
            {
                "id": step.id,
                "created_at": step.created_at,
                "trace_id": step.trace_id,
                "analysis_date": step.analysis_date,
                "workflow_name": step.workflow_name,
                "mcp_session_id": step.mcp_session_id,
                "iteration": step.iteration,
                "step_type": step.step_type,
                "action": step.action,
                "tool_name": step.tool_name,
                "skill_name": step.skill_name,
                "requested_tool_names_text": step.requested_tool_names_text,
                "requested_skill_names_text": step.requested_skill_names_text,
                "arguments_hash": step.arguments_hash,
                "arguments_text": step.arguments_text,
                "status": step.status,
                "duplicate_skipped": step.duplicate_skipped,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "duration_ms": step.duration_ms,
                "llm_response_text": step.llm_response_text,
                "error_message": step.error_message,
                "result_summary": step.result_summary,
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
