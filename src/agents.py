from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from time import monotonic
from typing import Any

from llm_core.exceptions import StructuredOutputError
from llm_core.protocols import LLMProvider
from llm_core.types import (
    GenerationOptions,
    LLMRequest,
    LLMResponse,
    Message,
    ResponseFormat,
    TextPart,
)
from pydantic import ValidationError

from assets_loader import load_json, load_markdown_bullets, load_markdown_mapping, load_text
from conf import settings
from exceptions import (
    LogAnalysisAgentError,
    LogAnalysisHistoryComparisonServiceMissingException,
    McpClientError,
)
from logging_config import get_logger
from mcp import McpWorkflowClient
from repositories import LLMCallRepository
from schemas import (
    CollectLogsArtifact,
    DeterministicToolResponseModel,
    GroupedErrorResponseModel,
    GroupErrorsArgumentsModel,
    GroupErrorsResponseModel,
    LogAnalysisAgentContext,
    LogAnalysisAllowedAction,
    LogAnalysisCompactCoverageSnapshot,
    LogAnalysisCurrentCoverage,
    LogAnalysisEvidenceMode,
    LogAnalysisFinalReport,
    LogAnalysisGroupedErrorComparison,
    LogAnalysisGroupedErrorEvidenceLabel,
    LogAnalysisGroupedErrorHistorySummary,
    LogAnalysisGroupedErrorRunFingerprint,
    LogAnalysisGroupedErrorSignal,
    LogAnalysisHistoryComparisonStatus,
    LogAnalysisLLMCallIn,
    LogAnalysisNextRequiredAction,
    LogAnalysisOut,
    LogAnalysisPreparedPrompt,
    LogAnalysisPromptCollectedProject,
    LogAnalysisPromptCollectedSource,
    LogAnalysisPromptCollection,
    LogAnalysisPromptCompactedEvidence,
    LogAnalysisPromptContext,
    LogAnalysisPromptEvidence,
    LogAnalysisPromptEvidenceKind,
    LogAnalysisPromptGroupedErrorComparison,
    LogAnalysisPromptGroupedErrorEvidence,
    LogAnalysisPromptGroupedErrorFingerprint,
    LogAnalysisPromptHistoryComparisonState,
    LogAnalysisPromptPhase,
    LogAnalysisSeverity,
    LogAnalysisSkillReadRequest,
    LogAnalysisSourceCoverageComparison,
    LogAnalysisToolCall,
    LogAnalysisToolCallRequest,
    LogAnalysisToolResult,
    LogCollectionWindow,
    LogSourceCollectionStatus,
    McpToolName,
    PreviousLogAnalysisContext,
    PreviousLogAnalysisPromptContext,
    ProjectManifestSummary,
    RecommendedAction,
    SnapshotAccessGuidance,
    WorkflowBootstrap,
    WorkflowSkill,
    WorkflowSkillContent,
    WorkflowTool,
)
from services.log_fingerprints import (
    LOG_ANALYSIS_FINGERPRINT_VERSION,
    LogAnalysisFingerprintBuilder,
    build_grouped_error_run,
)
from services.log_history_comparison import LogAnalysisHistoryComparisonService
from utils.grouped_errors import (
    attention_priority_rank,
    coalesce_grouped_errors,
    project_grouped_error_prompt_rows,
)
from utils.llm_context import (
    CONTEXT_LIMITATION,
    BoundedMessages,
    bound_messages,
    is_context_length_error,
    message_bytes,
    smaller_messages,
)
from utils.llm_usage import usage_cost_usd, usage_raw_json, usage_telemetry
from utils.runtime import dump_arguments, elapsed_ms, hash_text


class ReasoningEffort(StrEnum):
    """Application reasoning setting, mapped when llm-core supports it."""

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class TextVerbosity(StrEnum):
    """Application text-verbosity setting, mapped when llm-core supports it."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


logger = get_logger(__name__)
MAX_LLM_TOOL_LOOP_ITERATIONS = 5
# Transport batch size only; `_collect_all_group_error_pages` reads every page.
GROUP_ERRORS_PER_PAGE = 200
LOG_ANALYSIS_INSTRUCTIONS = load_markdown_bullets("log_analysis_instructions.md")
LOG_ANALYSIS_REPORT_CONTRACT = load_markdown_mapping("log_analysis_report_contract.md")
LOG_ANALYSIS_DECISION_SKILL = load_text("log_analysis_decision_skill.md")
LOG_ANALYSIS_CRITICAL_DECISION_RULES = load_text("log_analysis_critical_decision_rules.md")
HISTORICAL_CONTEXT_TEMPLATE = load_text("historical_context.md")
LOG_ANALYSIS_NO_COMPARE_HISTORY_PROMPT = load_json("log_analysis_no_compare_history_prompt.json")
LOG_ANALYSIS_COMPARE_HISTORY_PROMPT = load_json("log_analysis_compare_history_prompt.json")
POST_COLLECTION_EXCLUDED_TOOL_NAMES = frozenset(
    {
        McpToolName.ANALYZE_DAILY_LOG_BUNDLE,
        McpToolName.COLLECT_LOGS,
        McpToolName.GET_LOG_COLLECTION_STATUS,
        McpToolName.LIST_PROJECTS,
        McpToolName.START_LOG_COLLECTION,
        "get_mcp_health_check",
        "get_mcp_service_status",
    }
)


class LogAnalysisModelTier(StrEnum):
    """Deterministic model tiers used by log-analysis routing."""

    FAST = "fast"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class LogAnalysisModelRoute:
    """Selected tier and auditable deterministic reason codes."""

    tier: LogAnalysisModelTier
    reasons: tuple[str, ...]


def select_log_analysis_model_route(
    *,
    evidence: LogAnalysisPromptEvidence,
    current_coverage: LogAnalysisCurrentCoverage,
) -> LogAnalysisModelRoute:
    """Select fast only for complete clean or unchanged low-risk evidence."""

    current = evidence.current_grouped_errors
    if current is None or not current.available:
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("current_evidence_missing",))
    if not current.evidence_complete:
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("current_evidence_incomplete",))
    if any(
        (
            current_coverage.collection_warnings,
            current_coverage.unknown_requested_sources,
            current_coverage.zero_line_sources,
            current_coverage.unavailable_sources,
            current_coverage.truncated_sources,
            current_coverage.continuation_available_sources,
        )
    ):
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("coverage_gap_present",))

    attention_counts: dict[str, int] = current.attention_priority_counts
    if attention_counts.get("actionable", 0):
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("actionable_family_present",))
    if attention_counts.get("investigate", 0):
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("investigate_family_present",))
    if current.omitted_details is not None and current.omitted_details.counts_by_attention.get(
        "actionable", 0
    ):
        return LogAnalysisModelRoute(
            LogAnalysisModelTier.STRONG,
            ("actionable_detail_projection_invariant_failed",),
        )

    if current.unique_group_count == 0:
        return LogAnalysisModelRoute(LogAnalysisModelTier.FAST, ("complete_clean_evidence",))

    if evidence.kind != LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON:
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("stability_not_compared",))
    if (
        evidence.history_comparison is None
        or evidence.history_comparison.status != LogAnalysisHistoryComparisonStatus.AVAILABLE
    ):
        return LogAnalysisModelRoute(
            LogAnalysisModelTier.STRONG,
            ("history_comparison_unavailable",),
        )
    grouped_error_diff = (
        evidence.prompt_compacted.grouped_error_diff
        if evidence.prompt_compacted is not None
        else None
    )
    if grouped_error_diff is None or not grouped_error_diff.available:
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("grouped_error_diff_missing",))
    if grouped_error_diff.evidence_quality_warnings:
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("evidence_warning_present",))
    if not grouped_error_diff.resolved_high_severity_current_scope_covered:
        return LogAnalysisModelRoute(
            LogAnalysisModelTier.STRONG,
            ("resolved_high_severity_scope_gap",),
        )
    if grouped_error_diff.new_fingerprint_count:
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("new_family_present",))
    if grouped_error_diff.worsened_fingerprint_count:
        return LogAnalysisModelRoute(LogAnalysisModelTier.STRONG, ("worsened_family_present",))
    if (
        grouped_error_diff.new_fingerprint_count != len(grouped_error_diff.new_fingerprints)
        or grouped_error_diff.resolved_fingerprint_count
        != len(grouped_error_diff.resolved_fingerprints)
        or grouped_error_diff.worsened_fingerprint_count
        != len(grouped_error_diff.worsened_fingerprints)
        or grouped_error_diff.improved_fingerprint_count
        != len(grouped_error_diff.improved_fingerprints)
    ):
        return LogAnalysisModelRoute(
            LogAnalysisModelTier.STRONG,
            ("change_identity_mapping_incomplete",),
        )
    return LogAnalysisModelRoute(
        LogAnalysisModelTier.FAST,
        ("complete_stable_watch_or_routine_evidence",),
    )


class MonitoringWorkflowAgent:
    """Agent boundary for MCP-backed monitoring workflow bootstrap calls."""

    def __init__(
        self,
        mcp_client: McpWorkflowClient,
        llm_provider: LLMProvider,
        private_monitoring_context: str,
        history_comparison_service: LogAnalysisHistoryComparisonService | None = None,
        history_comparison_enabled: bool = False,
        fast_llm_provider: LLMProvider | None = None,
        fast_model_name: str | None = None,
        strong_model_name: str | None = None,
        strong_reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM,
        text_verbosity: TextVerbosity = TextVerbosity.LOW,
        max_output_tokens: int = 4_000,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm_provider = llm_provider
        self.strong_llm_provider = llm_provider
        self.fast_llm_provider = fast_llm_provider or llm_provider
        self.fast_model_name = fast_model_name
        self.strong_model_name = strong_model_name
        self.strong_reasoning_effort = strong_reasoning_effort
        self.text_verbosity = text_verbosity
        self.max_output_tokens = max_output_tokens
        self.private_monitoring_context = private_monitoring_context
        self.llm_call_repository: LLMCallRepository | None = None
        self.history_comparison_service = history_comparison_service
        self.history_comparison_enabled = history_comparison_enabled

        if history_comparison_enabled and not history_comparison_service:
            raise LogAnalysisHistoryComparisonServiceMissingException(
                "History comparison service is required when history comparison is enabled."
            )

    async def run_log_analysis(
        self,
        *,
        analysis_date: date,
        log_window: LogCollectionWindow,
        historical_context: str = "",
        previous_analysis: LogAnalysisOut | None = None,
    ) -> LogAnalysisAgentContext:
        """Orchestrate the daily log-analysis workflow."""

        logger.info(
            "loading MCP daily log workflow bundle",
            extra={"event": "workflow_bundle_load_start"},
        )
        workflow: WorkflowBootstrap = await self.mcp_client.get_workflow_bundle()
        mandatory_skills: list[WorkflowSkillContent] = await self._read_mandatory_skills(
            workflow.mandatory_skills
        )
        available_projects: list[ProjectManifestSummary] = await self._load_available_projects()
        current_logs: CollectLogsArtifact = await self._collect_current_logs(
            workflow=workflow,
            log_window=log_window,
        )
        previous_analysis_context: PreviousLogAnalysisContext | None = (
            PreviousLogAnalysisContext.from_analysis(previous_analysis)
            if previous_analysis is not None
            else None
        )
        preflight_grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint] = (
            await self._collect_preflight_grouped_error_runs(
                workflow=workflow,
                current_logs=current_logs,
            )
        )
        current_coverage_snapshot: dict[str, Any] = (
            LogAnalysisFingerprintBuilder.build_coverage_snapshot(current_logs)
        )
        prepared_evidence: LogAnalysisPromptEvidence = self._prepare_log_analysis_evidence(
            current_grouped_errors=preflight_grouped_error_runs,
            current_coverage_snapshot=current_coverage_snapshot,
            previous_analysis=previous_analysis_context,
        )
        self._log_history_mode_selection(
            analysis_date=analysis_date,
            previous_analysis=previous_analysis,
            prepared_evidence=prepared_evidence,
        )
        prompt: LogAnalysisPreparedPrompt = self._build_log_analysis_prompt(
            analysis_date=analysis_date,
            workflow=workflow,
            mandatory_skills=mandatory_skills,
            available_projects=available_projects,
            collect_logs=current_logs,
            private_monitoring_context=self.private_monitoring_context,
            historical_context=historical_context,
            previous_analysis=previous_analysis_context,
            prepared_evidence=prepared_evidence,
        )
        model_route: LogAnalysisModelRoute = select_log_analysis_model_route(
            evidence=prepared_evidence,
            current_coverage=prompt.context.current_coverage,
        )
        (
            final_report,
            tool_results,
            llm_tokens_used,
            llm_cost_usd,
            llm_report_execution_time_seconds,
        ) = await self._execute_llm_analysis(
            prompt=prompt,
            workflow=workflow,
            analysis_date=analysis_date,
            current_logs=current_logs,
            preflight_grouped_error_runs=preflight_grouped_error_runs,
            model_route=model_route,
        )
        logger.info(
            "completed log-analysis LLM tool loop",
            extra={
                "event": "log_analysis_llm_final_report_done",
                "workflow_name": workflow.workflow_name,
                "mandatory_skill_count": len(workflow.mandatory_skills),
                "optional_skill_count": len(workflow.optional_skills),
                "tool_count": len(workflow.tools),
                "available_project_count": len(available_projects),
                "collected_project_count": len(current_logs.projects),
                "tool_result_count": len(tool_results),
                "log_window_since": log_window.since,
                "log_window_until": log_window.until,
                "severity": final_report.severity,
                "model_route_tier": model_route.tier.value,
                "model_route_reasons": list(model_route.reasons),
                "llm_report_execution_time_seconds": llm_report_execution_time_seconds,
            },
        )
        return LogAnalysisAgentContext(
            workflow=workflow,
            collect_logs=current_logs,
            prompt=prompt,
            preflight_grouped_error_runs=preflight_grouped_error_runs,
            tool_results=tool_results,
            final_report=final_report,
            log_window_since=log_window.since_datetime,
            log_window_until=log_window.until_datetime,
            llm_tokens_used=llm_tokens_used,
            llm_cost_usd=llm_cost_usd,
            llm_report_execution_time_seconds=llm_report_execution_time_seconds,
        )

    async def _load_available_projects(self) -> list[ProjectManifestSummary]:
        """Load and validate the projects visible to the workflow caller."""

        available_projects: list[ProjectManifestSummary] = await self.mcp_client.list_projects()
        if available_projects:
            return available_projects
        raise McpClientError(
            (
                "MCP list_projects returned no projects for this workflow caller. "
                "Upload project manifests to MCP or check the caller project scope "
                "before collecting logs."
            ),
            mcp_url=self.mcp_client.base_url,
            tool_name=McpToolName.LIST_PROJECTS,
        )

    async def _collect_current_logs(
        self,
        *,
        workflow: WorkflowBootstrap,
        log_window: LogCollectionWindow,
    ) -> CollectLogsArtifact:
        """Collect current logs and reject truncated snapshots."""

        current_logs: CollectLogsArtifact = await self.mcp_client.collect_logs(
            since=log_window.since,
            until=log_window.until,
        )
        truncated_sources: list[str] = self._build_current_coverage(current_logs).truncated_sources
        if truncated_sources:
            raise LogAnalysisAgentError(
                "Current log collection is incomplete because sources were truncated: "
                + ", ".join(truncated_sources),
                workflow=workflow,
                collect_logs=current_logs,
            )
        return current_logs

    def _log_history_mode_selection(
        self,
        *,
        analysis_date: date,
        previous_analysis: LogAnalysisOut | None,
        prepared_evidence: LogAnalysisPromptEvidence,
    ) -> None:
        """Log the deterministic evidence mode selected for this run."""

        history_comparison_status: LogAnalysisHistoryComparisonStatus | None = (
            prepared_evidence.history_comparison.status
            if prepared_evidence.history_comparison is not None
            else None
        )
        grouped_error_comparison_available: bool = (
            prepared_evidence.prompt_compacted is not None
            and prepared_evidence.prompt_compacted.grouped_error_diff is not None
        )
        logger.info(
            "selected log-analysis history mode",
            extra={
                "event": "log_analysis_history_mode_selected",
                "analysis_date": str(analysis_date),
                "compare_history_enabled": self.history_comparison_enabled,
                "history_comparison_status": history_comparison_status,
                "previous_analysis_found": previous_analysis is not None,
                "evidence_kind": prepared_evidence.kind.value,
                "grouped_error_comparison_available": grouped_error_comparison_available,
                "llm_decision_mode": prepared_evidence.kind.value,
            },
        )

    async def _execute_llm_analysis(
        self,
        *,
        prompt: LogAnalysisPreparedPrompt,
        workflow: WorkflowBootstrap,
        analysis_date: date,
        current_logs: CollectLogsArtifact,
        preflight_grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint],
        model_route: LogAnalysisModelRoute,
    ) -> tuple[LogAnalysisFinalReport, list[LogAnalysisToolResult], int, float, float]:
        """Run the LLM tool loop and retain failure context and timing."""

        started_at: float = monotonic()
        try:
            final_report, tool_results, tokens_used, cost_usd = await self._run_tool_loop(
                prompt=prompt,
                workflow=workflow,
                analysis_date=analysis_date,
                mcp_session_id=current_logs.session_id,
                preflight_grouped_error_runs=preflight_grouped_error_runs,
                model_route=model_route,
            )
        except Exception as exc:
            raise LogAnalysisAgentError(
                str(exc),
                workflow=workflow,
                collect_logs=current_logs,
                prompt=prompt,
            ) from exc
        execution_time_seconds: float = round(monotonic() - started_at, 3)
        return final_report, tool_results, tokens_used, cost_usd, execution_time_seconds

    async def _read_mandatory_skills(
        self,
        skills: list[WorkflowSkill],
    ) -> list[WorkflowSkillContent]:
        """Fetch mandatory workflow skill resources before the first LLM call."""

        skill_contents: list[WorkflowSkillContent] = []
        for skill in skills:
            content: str = await self.mcp_client.read_resource(skill.resource_uri)
            skill_contents.append(
                WorkflowSkillContent(
                    name=skill.name,
                    resource_uri=skill.resource_uri,
                    description=skill.description,
                    content=content,
                )
            )
        return skill_contents

    def _prepare_log_analysis_evidence(
        self,
        *,
        current_grouped_errors: list[LogAnalysisGroupedErrorRunFingerprint],
        current_coverage_snapshot: dict[str, Any],
        previous_analysis: PreviousLogAnalysisContext | None,
    ) -> LogAnalysisPromptEvidence:
        """Prepare the single prompt evidence object from previous/current facts.

        Current grouped errors are always compacted first, because they are the
        deterministic view of today's log window. If no previous analysis exists,
        the prompt receives a current-only grouped-error baseline. If history
        comparison is enabled and previous data exists, the prompt receives a
        compact deterministic diff plus source-coverage comparison. Otherwise,
        the prompt receives previous/current grouped-error baselines without a
        Python-computed diff.
        """

        current_grouped_error_evidence: LogAnalysisPromptGroupedErrorEvidence | None = (
            self._compact_grouped_error_baseline_for_prompt(
                label=LogAnalysisGroupedErrorEvidenceLabel.CURRENT,
                groups=[group for run in current_grouped_errors for group in run.result.groups],
                run_count=len(current_grouped_errors),
                tool_scope_by_project=(
                    LogAnalysisHistoryComparisonService.build_grouped_error_run_scope_by_project(
                        current_grouped_errors
                    )
                ),
                rationale=(
                    "Current grouped-error fingerprints collected from today's log window. "
                    "This is current deterministic evidence, but not a Python history diff."
                ),
                grouped_error_runs=current_grouped_errors,
            )
        )
        coverage_totals = current_coverage_snapshot.get("totals", {})
        if (
            current_grouped_error_evidence is not None
            and isinstance(coverage_totals, dict)
            and _int_or_zero(coverage_totals.get("truncated_sources")) > 0
        ):
            current_grouped_error_evidence = current_grouped_error_evidence.model_copy(
                update={"evidence_complete": False}
            )
        if previous_analysis is None:
            return LogAnalysisPromptEvidence(
                kind=LogAnalysisPromptEvidenceKind.GROUPED_ERROR_BASELINE,
                decision_prompt=LOG_ANALYSIS_NO_COMPARE_HISTORY_PROMPT,
                previous_grouped_errors=None,
                current_grouped_errors=current_grouped_error_evidence,
            )

        if self.history_comparison_enabled:
            history_comparison_status: LogAnalysisHistoryComparisonStatus
            source_coverage_comparison: LogAnalysisSourceCoverageComparison
            compact_grouped_error_comparison: LogAnalysisPromptGroupedErrorComparison | None
            (
                history_comparison_status,
                source_coverage_comparison,
                compact_grouped_error_comparison,
            ) = self.prepare_history_comparison_evidence_context(
                previous_analysis=previous_analysis,
                current_grouped_errors=current_grouped_errors,
                current_coverage_snapshot=current_coverage_snapshot,
            )
            return self._build_history_comparison_evidence_prompt(
                history_comparison_status=history_comparison_status,
                source_coverage_comparison=source_coverage_comparison,
                grouped_error_comparison=compact_grouped_error_comparison,
                current_grouped_errors=current_grouped_error_evidence,
            )

        previous_groups: list[LogAnalysisGroupedErrorSignal] = (
            self._extract_grouped_error_signals_from_previous_analysis(previous_analysis)
        )
        previous_grouped_error_evidence = self._compact_grouped_error_baseline_for_prompt(
            label=LogAnalysisGroupedErrorEvidenceLabel.PREVIOUS,
            groups=previous_groups,
            run_count=len(previous_analysis.fingerprints.grouped_error_runs),
            tool_scope_by_project=(
                LogAnalysisHistoryComparisonService.build_grouped_error_run_scope_by_project(
                    previous_analysis.fingerprints.grouped_error_runs
                )
            ),
            rationale=(
                "Previous grouped-error fingerprints from the stored log-analysis DB object. "
                "Use as historical baseline evidence, not as current log evidence."
            ),
            grouped_error_runs=previous_analysis.fingerprints.grouped_error_runs,
        )
        if (
            previous_grouped_error_evidence is not None
            and _int_or_zero(previous_analysis.coverage_snapshot.totals.get("truncated_sources"))
            > 0
        ):
            previous_grouped_error_evidence = previous_grouped_error_evidence.model_copy(
                update={"evidence_complete": False}
            )
        return LogAnalysisPromptEvidence(
            kind=LogAnalysisPromptEvidenceKind.GROUPED_ERROR_BASELINE,
            decision_prompt=LOG_ANALYSIS_NO_COMPARE_HISTORY_PROMPT,
            previous_grouped_errors=previous_grouped_error_evidence,
            current_grouped_errors=current_grouped_error_evidence,
        )

    def prepare_history_comparison_evidence_context(
        self,
        *,
        previous_analysis: PreviousLogAnalysisContext,
        current_grouped_errors: list[LogAnalysisGroupedErrorRunFingerprint],
        current_coverage_snapshot: dict[str, Any],
    ) -> tuple[
        LogAnalysisHistoryComparisonStatus,
        LogAnalysisSourceCoverageComparison,
        LogAnalysisPromptGroupedErrorComparison | None,
    ]:
        """Build the comparison context used by history-comparison prompt evidence.

        This is the history-comparison feature branch, not generic evidence
        preparation. It compares stored previous grouped errors with the current
        grouped-error baseline, compares source coverage snapshots, and compacts
        the diff before `_prepare_log_analysis_evidence` builds the final prompt
        evidence object.
        """

        history_service = self.history_comparison_service
        source_coverage_comparison: LogAnalysisSourceCoverageComparison = (
            history_service.build_missing_source_comparison(  # type: ignore[union-attr]
                previous_coverage_snapshot=previous_analysis.coverage_snapshot.model_dump(
                    mode="json"
                ),
                current_coverage_snapshot=current_coverage_snapshot,
                previous_severity=previous_analysis.severity,
            )
        )
        previous_evidence_incomplete: bool = any(
            run.result.truncated for run in previous_analysis.fingerprints.grouped_error_runs
        ) or (_int_or_zero(previous_analysis.coverage_snapshot.totals.get("truncated_sources")) > 0)
        if previous_evidence_incomplete:
            return (
                LogAnalysisHistoryComparisonStatus.UNAVAILABLE,
                source_coverage_comparison.model_copy(
                    update={
                        "recommended_action": RecommendedAction.LLM_MAY_DECIDE,
                        "rationale": (
                            "Structured history comparison is unavailable because previous "
                            "log evidence is incomplete. "
                            "Use complete current evidence only and do not claim a trend."
                        ),
                    }
                ),
                None,
            )
        if previous_analysis.fingerprints.version != LOG_ANALYSIS_FINGERPRINT_VERSION:
            return (
                LogAnalysisHistoryComparisonStatus.UNAVAILABLE,
                source_coverage_comparison.model_copy(
                    update={
                        "recommended_action": RecommendedAction.LLM_MAY_DECIDE,
                        "rationale": (
                            "Structured history comparison is unavailable because the "
                            "previous semantic fingerprint format is not comparable. "
                            "Use complete current evidence and do not claim a trend."
                        ),
                    }
                ),
                None,
            )
        grouped_error_comparison: LogAnalysisGroupedErrorComparison | None = (
            self.history_comparison_service.compare_grouped_errors(  # type: ignore[union-attr]
                previous_grouped_errors=previous_analysis.fingerprints.grouped_error_runs,
                current_grouped_errors=current_grouped_errors,
            )
        )
        if (
            grouped_error_comparison is not None
            and source_coverage_comparison.recommended_action == RecommendedAction.CALL_TOOLS
        ):
            source_coverage_comparison = source_coverage_comparison.model_copy(
                update={
                    "recommended_action": RecommendedAction.LLM_MAY_DECIDE,
                    "tool_scope_by_project": {},
                    "rationale": (
                        "Current grouped-error evidence already covers every available "
                        "source. Other-source tools cannot replace changed or missing "
                        "coverage: report the gap and limit trend claims. Call another "
                        "tool only for a separate material question."
                    ),
                }
            )
        compact_grouped_error_comparison: LogAnalysisPromptGroupedErrorComparison | None = None
        if grouped_error_comparison is not None:
            compact_grouped_error_comparison = self.history_comparison_service.compact_grouped_error_comparison_for_prompt(  # type: ignore[union-attr]  # noqa: E501
                grouped_error_comparison
            )
        return (
            LogAnalysisHistoryComparisonStatus.AVAILABLE,
            source_coverage_comparison,
            compact_grouped_error_comparison,
        )

    def _build_log_analysis_prompt(
        self,
        *,
        analysis_date: date,
        workflow: WorkflowBootstrap,
        mandatory_skills: list[WorkflowSkillContent],
        available_projects: list[ProjectManifestSummary],
        collect_logs: CollectLogsArtifact,
        private_monitoring_context: str,
        historical_context: str,
        previous_analysis: PreviousLogAnalysisContext | None,
        prepared_evidence: LogAnalysisPromptEvidence,
    ) -> LogAnalysisPreparedPrompt:
        """Build the one initial structured prompt for the log-analysis LLM loop.

        This method is called once per log-analysis run, after MCP collection
        and optional deterministic history comparison. It does not call MCP
        tools and it does not run the LLM. Instead, it converts deterministic
        facts into prompt fields that the later tool loop can enforce.

        When `--no-compare-history` intentionally skips code-level
        previous-vs-current comparison, prepared_evidence already contains
        grouped-error baseline evidence instead of history-comparison evidence.

        The source coverage comparison and previous severity are prompt context,
        not the core decision-maker. When deterministic comparison is enabled,
        current grouped-error comparison results become risk signals that the
        LLM must interpret: it may return final_report when the evidence is
        enough, or call more tools when uncertainty, impact, or scope requires
        it.
        """

        previous_analysis_context: PreviousLogAnalysisContext | None = previous_analysis
        prompt_previous_analysis_context = (
            self._compact_previous_analysis_for_prompt(previous_analysis_context)
            if previous_analysis_context is not None
            else None
        )
        prompt_compacted: LogAnalysisPromptCompactedEvidence | None = (
            prepared_evidence.prompt_compacted
        )
        source_coverage_recommends_tools: bool = (
            prompt_compacted is not None
            and prompt_compacted.source_coverage is not None
            and prompt_compacted.source_coverage.recommended_action == RecommendedAction.CALL_TOOLS
        )
        current_grouped_errors = prepared_evidence.current_grouped_errors
        current_grouped_evidence_available = bool(
            current_grouped_errors is not None and current_grouped_errors.evidence_complete
        )
        evidence_mode: LogAnalysisEvidenceMode
        if current_grouped_evidence_available:
            evidence_mode = LogAnalysisEvidenceMode.CURRENT_GROUPED_ERRORS_AVAILABLE
        elif (
            prepared_evidence.kind != LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON
            and previous_analysis_context is not None
        ):
            evidence_mode = LogAnalysisEvidenceMode.METADATA_AND_PREVIOUS_ANALYSIS_ONLY
        elif source_coverage_recommends_tools:
            evidence_mode = LogAnalysisEvidenceMode.SOURCE_COVERAGE_CHANGED_REQUIRES_TOOLS
        elif previous_analysis_context is not None and previous_analysis_context.severity in {
            LogAnalysisSeverity.WARNING,
            LogAnalysisSeverity.CRITICAL,
        }:
            evidence_mode = LogAnalysisEvidenceMode.HISTORY_GUARD_REQUIRES_TOOLS
        else:
            evidence_mode = LogAnalysisEvidenceMode.MCP_TOOL_RESULTS_REQUIRED

        next_required_action: LogAnalysisNextRequiredAction
        if current_grouped_evidence_available:
            next_required_action = LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
        else:
            next_required_action = LogAnalysisNextRequiredAction.CALL_TOOLS

        return LogAnalysisPreparedPrompt(
            system_prompt=self._build_system_prompt_with_mandatory_skills(
                workflow=workflow,
                mandatory_skills=mandatory_skills,
                private_monitoring_context=private_monitoring_context,
                historical_context=historical_context,
            ),
            context=LogAnalysisPromptContext(
                analysis_date=analysis_date,
                workflow_name=workflow.workflow_name,
                current_phase=LogAnalysisPromptPhase.INSPECT_COLLECTED_LOGS,
                completed_steps=[
                    McpToolName.ANALYZE_DAILY_LOG_BUNDLE,
                    "read_mandatory_skills",
                    McpToolName.LIST_PROJECTS,
                    McpToolName.COLLECT_LOGS,
                ],
                historical_context_available=bool(historical_context),
                evidence=prepared_evidence.to_prompt_dict(),
                previous_analysis=prompt_previous_analysis_context,
                current_coverage=self._build_current_coverage(collect_logs),
                evidence_mode=evidence_mode,
                current_tool_result_count=0,
                trend_summary_instruction=_build_trend_summary_instruction(
                    historical_context_available=bool(historical_context)
                ),
                allowed_actions=[
                    LogAnalysisAllowedAction.CALL_TOOLS,
                    LogAnalysisAllowedAction.READ_SKILLS,
                    LogAnalysisAllowedAction.FINAL_REPORT,
                ],
                next_required_action=next_required_action,
                final_report_allowed=current_grouped_evidence_available,
                available_projects=available_projects,
                loaded_mandatory_skill_names=[skill.name for skill in mandatory_skills],
                optional_skills=workflow.optional_skills,
                collection=self._build_prompt_collection(collect_logs),
                snapshot_access=SnapshotAccessGuidance(
                    workspace=collect_logs.workspace,
                    session_id=collect_logs.session_id,
                    session_id_is_for_session_workspace_only=True,
                    workflow_followup_arguments=["project_name", "archive_name"],
                    instruction=(
                        "This collection is a workflow snapshot. Use project_name for "
                        "workflow follow-up tools. Ignore session_id unless a later "
                        "collection explicitly uses workspace='session'."
                    ),
                ),
                available_tools=self._project_post_collection_tools(workflow.tools),
                report_contract=LOG_ANALYSIS_REPORT_CONTRACT,
            ),
        )

    @staticmethod
    def _project_post_collection_tools(tools: list[WorkflowTool]) -> list[WorkflowTool]:
        """Return only tools that remain useful after initial log collection."""

        return [tool for tool in tools if tool.tool_name not in POST_COLLECTION_EXCLUDED_TOOL_NAMES]

    @staticmethod
    def _build_mode_specific_log_analysis_instructions(
        *,
        history_comparison_enabled: bool,
    ) -> list[str]:
        """Return common instructions plus only the active evidence-mode rules."""

        if not history_comparison_enabled:
            excluded_markers: tuple[str, ...] = (
                "history_comparison.",
                "history_comparison.status",
                "grouped_error_diff",
                "compare-history",
                "deterministic history comparison",
            )
            mode_rules = LOG_ANALYSIS_NO_COMPARE_HISTORY_PROMPT.get("decision_rules", [])
        else:
            excluded_markers = (
                "history_baseline",
                "grouped_error_baseline",
                "disabled-history",
                "previous_grouped_errors and current_grouped_errors",
            )
            mode_rules = LOG_ANALYSIS_COMPARE_HISTORY_PROMPT.get("decision_rules", [])

        common_instructions: list[str] = [
            instruction
            for instruction in LOG_ANALYSIS_INSTRUCTIONS
            if not any(marker in instruction for marker in excluded_markers)
        ]
        return [
            *common_instructions,
            *(rule for rule in mode_rules if isinstance(rule, str)),
        ]

    @staticmethod
    def _build_history_comparison_evidence_prompt(
        *,
        history_comparison_status: LogAnalysisHistoryComparisonStatus,
        source_coverage_comparison: LogAnalysisSourceCoverageComparison | None,
        grouped_error_comparison: LogAnalysisPromptGroupedErrorComparison | None,
        current_grouped_errors: LogAnalysisPromptGroupedErrorEvidence | None,
    ) -> LogAnalysisPromptEvidence:
        """Build prompt evidence for deterministic history-comparison mode."""

        return LogAnalysisPromptEvidence(
            kind=LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON,
            decision_prompt=LOG_ANALYSIS_COMPARE_HISTORY_PROMPT,
            history_comparison=LogAnalysisPromptHistoryComparisonState(
                status=history_comparison_status,
            ),
            current_grouped_errors=current_grouped_errors,
            prompt_compacted=LogAnalysisPromptCompactedEvidence(
                source_coverage=source_coverage_comparison,
                grouped_error_diff=grouped_error_comparison,
            ),
        )

    @staticmethod
    def _compact_previous_analysis_for_prompt(
        previous_analysis: PreviousLogAnalysisContext,
    ) -> PreviousLogAnalysisPromptContext:
        """Return previous DB analysis without source-level coverage rows."""

        previous_groups: list[LogAnalysisGroupedErrorSignal] = [
            group
            for run in previous_analysis.fingerprints.grouped_error_runs
            for group in run.result.groups
        ]
        grouped_signal_count: int = len(previous_groups)
        grouped_run_count: int = len(previous_analysis.fingerprints.grouped_error_runs)
        grouped_error_history_summary: LogAnalysisGroupedErrorHistorySummary | None = None
        if grouped_signal_count or grouped_run_count:
            grouped_error_history_summary = LogAnalysisGroupedErrorHistorySummary(
                signal_count=grouped_signal_count,
                run_count=grouped_run_count,
                detail="Full grouped-error history is included as previous fingerprint baseline.",
            )
        fingerprints = previous_analysis.fingerprints.model_copy(
            deep=True,
            update={
                "grouped_error_runs": [],
                "grouped_error_history_summary": grouped_error_history_summary,
            },
        )
        return PreviousLogAnalysisPromptContext(
            analysis_date=previous_analysis.analysis_date,
            summary=previous_analysis.summary,
            severity=previous_analysis.severity,
            trend_summary=previous_analysis.trend_summary,
            fingerprints=fingerprints,
            evidence_fingerprints=previous_analysis.evidence_fingerprints,
            known_patterns=previous_analysis.known_patterns,
            coverage_snapshot=LogAnalysisCompactCoverageSnapshot(
                totals=previous_analysis.coverage_snapshot.totals
            ),
            fingerprint_version=previous_analysis.fingerprint_version,
        )

    @staticmethod
    def _extract_grouped_error_signals_from_previous_analysis(
        previous_analysis: PreviousLogAnalysisContext,
    ) -> list[LogAnalysisGroupedErrorSignal]:
        """Return grouped-error signals stored in the previous DB analysis."""

        return [
            group
            for run in previous_analysis.fingerprints.grouped_error_runs
            for group in run.result.groups
        ]

    @staticmethod
    def _compact_grouped_error_baseline_for_prompt(
        *,
        label: LogAnalysisGroupedErrorEvidenceLabel,
        groups: list[LogAnalysisGroupedErrorSignal],
        run_count: int,
        tool_scope_by_project: dict[str, list[str]],
        rationale: str,
        grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint] | None = None,
        detail_limit_per_attention: int | None = None,
    ) -> LogAnalysisPromptGroupedErrorEvidence | None:
        """Return complete aggregates plus a bounded detailed family ledger."""

        if run_count == 0 and not groups:
            return None
        coalesced = coalesce_grouped_errors(groups)

        fingerprints = sorted(
            [
                LogAnalysisPromptGroupedErrorFingerprint(
                    fingerprint=family.signal.fingerprint,
                    project_name=family.signal.project_name,
                    category=family.signal.category,
                    severity=family.signal.severity,
                    source_keys=family.signal.source_keys,
                    status_codes=family.signal.status_codes,
                    count=family.signal.count,
                    request_paths=list(family.semantics.normalized_paths),
                    request_methods=list(family.semantics.request_methods),
                    request_hosts=list(family.semantics.request_hosts),
                    message_summary=family.signal.message_summary,
                    upstream_attempted=family.signal.upstream_attempted,
                    attention_priority=family.semantics.attention_priority,
                    variant_count=family.variant_count,
                )
                for family in coalesced.values()
            ],
            key=lambda item: (
                attention_priority_rank(item.attention_priority),
                (
                    0
                    if item.severity.casefold() in {"high", "critical"}
                    or any(status_code >= 500 for status_code in item.status_codes)
                    else 1
                ),
                -item.count,
                item.project_name,
                item.fingerprint,
            ),
        )
        severity_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        status_code_counts: dict[str, int] = {}
        source_key_counts: dict[str, int] = {}
        attention_priority_counts: dict[str, int] = {}
        attention_priority_event_counts: dict[str, int] = {}
        for family in fingerprints:
            severity: str = family.severity or "unknown"
            category: str = family.category or "unknown"
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1
            priority = family.attention_priority.value
            attention_priority_counts[priority] = attention_priority_counts.get(priority, 0) + 1
            attention_priority_event_counts[priority] = (
                attention_priority_event_counts.get(priority, 0) + family.count
            )
            for status_code in family.status_codes:
                status_code_key: str = str(status_code)
                status_code_counts[status_code_key] = status_code_counts.get(status_code_key, 0) + 1
            for source_key in family.source_keys:
                source_key_counts[source_key] = source_key_counts.get(source_key, 0) + 1
        runs = grouped_error_runs or []
        event_count = sum(family.count for family in fingerprints)
        detailed_fingerprints, omitted_details = project_grouped_error_prompt_rows(
            fingerprints,
            detail_limit_per_attention=detail_limit_per_attention,
        )
        return LogAnalysisPromptGroupedErrorEvidence(
            available=True,
            label=label,
            tool_scope_by_project=tool_scope_by_project,
            run_count=run_count,
            group_count=len(groups),
            unique_group_count=len(fingerprints),
            event_count=event_count,
            evidence_complete=not any(run.result.truncated for run in runs),
            attention_priority_counts=attention_priority_counts,
            attention_priority_event_counts=attention_priority_event_counts,
            severity_counts=severity_counts,
            category_counts=category_counts,
            status_code_counts=status_code_counts,
            source_key_counts=source_key_counts,
            fingerprints=detailed_fingerprints,
            omitted_details=omitted_details,
            rationale=(
                f"{rationale} Aggregate counts and family identities are complete. "
                "Detailed rows are bounded per attention band; omitted_details retains "
                "every remaining family identity for targeted deterministic follow-up."
            ),
        )

    @staticmethod
    def _build_group_errors_arguments_from_current_logs(
        current_logs: CollectLogsArtifact,
    ) -> list[GroupErrorsArgumentsModel]:
        """Build scoped `group_errors` arguments from the current log collection.

        `collect_logs` tells us which project sources produced snapshot files,
        while `group_errors` expects explicit MCP arguments. This bridge keeps
        the pre-LLM grouped-error baseline scoped to usable snapshots instead of
        asking MCP to inspect unavailable sources.
        """

        arguments_list: list[GroupErrorsArgumentsModel] = []
        for project in sorted(current_logs.projects, key=lambda item: item.project_name):
            project_name: str = project.project_name
            if not project_name:
                continue
            resolved_source_keys: set[str] = set(project.resolved_source_keys)
            source_keys: list[str] = sorted(
                {
                    source.source_key
                    for source in project.sources
                    if source.source_key
                    and source.source_key in resolved_source_keys
                    and source.status == LogSourceCollectionStatus.COLLECTED
                    and source.output_file
                }
            )
            if not source_keys:
                continue
            arguments = GroupErrorsArgumentsModel(
                project_name=project_name,
                source_keys=source_keys,
                max_groups=GROUP_ERRORS_PER_PAGE,
            )
            arguments_list.append(arguments)
        return arguments_list

    async def _collect_all_group_error_pages(
        self,
        *,
        arguments: GroupErrorsArgumentsModel,
    ) -> GroupErrorsResponseModel:
        """Read every group_errors page without an arbitrary ceiling."""

        arguments_payload: dict[str, Any] = arguments.model_dump(
            mode="json",
            exclude_defaults=True,
            exclude_none=True,
        )
        response: GroupErrorsResponseModel = await self.mcp_client.call_deterministic_tool(
            McpToolName.GROUP_ERRORS,
            arguments_payload,
            response_model=GroupErrorsResponseModel,
        )
        if response.project_name != arguments.project_name:
            raise ValueError("group_errors returned an unexpected project scope")
        requested_source_keys: set[str] = set(arguments.source_keys or [])
        if arguments.source_key:
            requested_source_keys.add(arguments.source_key)
        if not requested_source_keys <= set(response.searched_source_keys):
            raise ValueError("group_errors returned a narrower source scope than requested")

        groups: list[GroupedErrorResponseModel] = []
        page = response
        offset: int = arguments.offset
        snapshot_identity = (
            response.fingerprint_version,
            response.project_name,
            response.workspace,
            response.session_id,
            response.snapshot_collected_at,
            response.snapshot_dir,
            response.grouped_error_count,
            response.searched_source_keys,
        )

        while True:
            if (
                page.fingerprint_version,
                page.project_name,
                page.workspace,
                page.session_id,
                page.snapshot_collected_at,
                page.snapshot_dir,
                page.grouped_error_count,
                page.searched_source_keys,
            ) != snapshot_identity:
                raise ValueError("group_errors snapshot changed during pagination")
            if page.offset != offset:
                raise ValueError("group_errors returned a non-contiguous page")

            groups.extend(page.groups)
            next_offset = page.next_offset
            if page.truncated:
                if (
                    not page.groups
                    or next_offset != offset + len(page.groups)
                    or next_offset >= response.grouped_error_count
                ):
                    raise ValueError("group_errors returned an invalid next_offset")
                offset = next_offset
                page = await self.mcp_client.call_deterministic_tool(
                    McpToolName.GROUP_ERRORS,
                    {**arguments_payload, "offset": offset},
                    response_model=GroupErrorsResponseModel,
                )
                continue
            break

        if response.grouped_error_count != len(groups):
            raise ValueError(
                "group_errors pagination finished with a mismatched grouped_error_count"
            )
        return response.model_copy(
            update={
                "groups": groups,
                "offset": 0,
                "returned_group_count": len(groups),
                "next_offset": len(groups),
                "partial_page": not response.analysis_complete,
                "truncated": False,
            }
        )

    async def _collect_preflight_grouped_error_runs(
        self,
        *,
        workflow: WorkflowBootstrap,
        current_logs: CollectLogsArtifact,
    ) -> list[LogAnalysisGroupedErrorRunFingerprint]:
        """Collect current grouped-error evidence before the LLM decision."""

        group_errors_arguments: list[GroupErrorsArgumentsModel] = (
            self._build_group_errors_arguments_from_current_logs(current_logs)
        )
        if not group_errors_arguments:
            raise LogAnalysisAgentError(
                "Current log collection contains no usable collected source snapshots.",
                workflow=workflow,
                collect_logs=current_logs,
            )

        grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint] = []
        try:
            for arguments in group_errors_arguments:
                response: GroupErrorsResponseModel = await self._collect_all_group_error_pages(
                    arguments=arguments,
                )
                arguments_payload: dict[str, Any] = arguments.model_dump(
                    mode="json",
                    exclude_defaults=True,
                    exclude_none=True,
                )
                structured_content: dict[str, Any] = response.model_dump(
                    mode="json",
                    exclude_unset=True,
                )
                grouped_error_runs.append(
                    build_grouped_error_run(
                        arguments=arguments_payload,
                        structured_content=structured_content,
                    )
                )
        except Exception as exc:
            raise LogAnalysisAgentError(
                str(exc),
                workflow=workflow,
                collect_logs=current_logs,
            ) from exc
        return grouped_error_runs

    @staticmethod
    def _build_system_prompt_with_mandatory_skills(
        *,
        workflow: WorkflowBootstrap,
        mandatory_skills: list[WorkflowSkillContent],
        private_monitoring_context: str,
        historical_context: str,
    ) -> str:
        """Append private VPS context and mandatory skills to the MCP-owned prompt."""

        skill_sections: list[str] = []
        for skill in mandatory_skills:
            skill_sections.append(
                "\n".join(
                    [
                        f"## {skill.name}",
                        "",
                        skill.content,
                    ]
                )
            )
        mandatory_skill_prompt: str = "\n\n".join(skill_sections)
        historical_section: str = ""
        if historical_context:
            historical_section = HISTORICAL_CONTEXT_TEMPLATE.format(
                historical_data=historical_context
            )
        return "\n\n".join(
            part
            for part in [
                workflow.prompt.strip(),
                LOG_ANALYSIS_CRITICAL_DECISION_RULES,
                "# Mandatory Workflow Skills",
                mandatory_skill_prompt.strip(),
                historical_section.strip(),
                "# Private Monitoring Context",
                private_monitoring_context.strip(),
            ]
            if part
        )

    async def _run_tool_loop(
        self,
        *,
        prompt: LogAnalysisPreparedPrompt,
        workflow: WorkflowBootstrap,
        analysis_date: date,
        mcp_session_id: str | None = None,
        preflight_grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint] | None = None,
        model_route: LogAnalysisModelRoute,
    ) -> tuple[LogAnalysisFinalReport, list[LogAnalysisToolResult], int, float]:
        """Run the LLM action loop until a final report is produced."""

        messages: list[Message] = [
            Message.from_text("system", prompt.system_prompt),
            Message.from_text("user", prompt.user_prompt),
        ]
        tool_results: list[LogAnalysisToolResult] = []
        fetched_skill_names: set[str] = set()
        executed_mcp_tool_calls: set[str] = {
            self._build_mcp_tool_call_key(
                LogAnalysisToolCall(
                    tool_name=McpToolName.GROUP_ERRORS,
                    arguments=dict(run.arguments),
                )
            )
            for run in preflight_grouped_error_runs or []
        }
        llm_tokens_used: int = 0
        llm_cost_usd: float = 0.0
        force_strong: bool = model_route.tier is LogAnalysisModelTier.STRONG
        for iteration in range(1, MAX_LLM_TOOL_LOOP_ITERATIONS + 1):
            # New tool results can increase the immutable snapshot floor.
            input_byte_budget: int = settings.LOG_ANALYSIS_LLM_MAX_INPUT_BYTES
            model_tier: LogAnalysisModelTier = (
                LogAnalysisModelTier.STRONG if force_strong else LogAnalysisModelTier.FAST
            )
            llm_provider: LLMProvider = (
                self.strong_llm_provider
                if model_tier is LogAnalysisModelTier.STRONG
                else self.fast_llm_provider
            )
            requested_model_name: str | None = (
                self.strong_model_name
                if model_tier is LogAnalysisModelTier.STRONG
                else self.fast_model_name
            )
            # A self-contained snapshot avoids hidden history growth in Responses.
            # Only the prompt projection is bounded; raw artifacts remain untouched.
            llm_response: LLMResponse | None = None
            for context_attempt in range(4):
                snapshot: BoundedMessages = bound_messages(messages, max_bytes=input_byte_budget)
                request_messages: list[Message] = snapshot.messages
                request_character_count: int = self._message_character_count(request_messages)
                try:
                    llm_response = self._request_llm_action(
                        messages=request_messages,
                        workflow=workflow,
                        analysis_date=analysis_date,
                        iteration=iteration,
                        llm_provider=llm_provider,
                        model_name=requested_model_name,
                        model_tier=model_tier,
                        route_reasons=model_route.reasons,
                    )
                    break
                except StructuredOutputError as exc:
                    await self._record_llm_step(
                        LogAnalysisLLMCallIn(
                            analysis_date=analysis_date,
                            workflow_name=workflow.workflow_name,
                            mcp_session_id=mcp_session_id,
                            iteration=iteration,
                            step_type="llm_call",
                            status="failed",
                            provider_name=llm_provider.name,
                            model_name=requested_model_name,
                            request_character_count=request_character_count,
                            error_message=str(exc),
                        )
                    )
                    if model_tier is not LogAnalysisModelTier.FAST:
                        raise
                    force_strong = True
                    messages[2:] = [self._build_invalid_fast_response_message(exc)]
                    break
                except Exception as exc:
                    if not is_context_length_error(exc):
                        raise
                    if context_attempt == 3:
                        raise ValueError(
                            "LLM context limit exceeded after three smaller-snapshot retries. "
                            "Reduce LOG_ANALYSIS_LLM_MAX_INPUT_BYTES or narrow analysis scope."
                        ) from exc
                    smaller_snapshot = smaller_messages(
                        messages, rejected_bytes=message_bytes(request_messages)
                    )
                    input_byte_budget = message_bytes(smaller_snapshot.messages)
                    logger.warning(
                        "retrying LLM action with a smaller evidence snapshot",
                        extra={
                            "event": "log_analysis_llm_context_retry",
                            "iteration": iteration,
                            "context_attempt": context_attempt + 1,
                            "input_byte_budget": input_byte_budget,
                            "model": requested_model_name,
                        },
                    )
            if llm_response is None:
                continue
            usage = llm_response.usage
            actual_model_name: str | None = llm_response.model_name or requested_model_name
            if usage is not None:
                llm_tokens_used += usage.total_tokens
                usage_cost: float | None = usage_cost_usd(
                    usage,
                    model_name=actual_model_name,
                )
                if usage_cost is not None:
                    llm_cost_usd += usage_cost

            prompt_tokens: int | None = usage.prompt_tokens if usage is not None else None
            completion_tokens: int | None = usage.completion_tokens if usage is not None else None
            total_tokens: int | None = usage.total_tokens if usage is not None else None
            call_cost_usd: float | None = (
                usage_cost_usd(usage, model_name=actual_model_name) if usage is not None else None
            )
            usage_raw: dict[str, Any] | None = usage_raw_json(usage) if usage is not None else None

            llm_step = LogAnalysisLLMCallIn(
                analysis_date=analysis_date,
                workflow_name=workflow.workflow_name,
                mcp_session_id=mcp_session_id,
                iteration=iteration,
                step_type="llm_call",
                status="succeeded",
                llm_response_text=llm_response.text or "",
                provider_name=llm_response.provider_name or llm_provider.name,
                model_name=actual_model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=call_cost_usd,
                request_character_count=request_character_count,
                usage_raw=usage_raw,
                result_summary=(
                    f"model_route={model_tier.value};" f"reasons={','.join(model_route.reasons)}"
                ),
            )
            payload: dict[str, Any] = {}
            action: str = ""
            final_report: LogAnalysisFinalReport | None = None
            tool_request: LogAnalysisToolCallRequest | None = None
            skill_request: LogAnalysisSkillReadRequest | None = None
            try:
                payload = self._extract_llm_payload(llm_response)
                raw_action: object = payload.get("action")
                action = str(raw_action or "")
                if raw_action == "final_report":
                    final_report = self._build_final_report_payload(payload)
                elif raw_action == "call_tools":
                    tool_request = self._build_tool_call_request(payload)
                elif raw_action == "read_skills":
                    skill_request = self._build_skill_read_request(payload)
                else:
                    raise ValueError("LLM action did not match expected shape.")
            except Exception as exc:
                await self._record_llm_step(
                    llm_step.model_copy(
                        update={
                            "action": action,
                            "status": "failed",
                            "error_message": str(exc),
                        }
                    )
                )
                if model_tier is LogAnalysisModelTier.FAST:
                    force_strong = True
                    messages[2:] = [self._build_invalid_fast_response_message(exc)]
                    continue
                raise
            await self._record_llm_step(llm_step.model_copy(update={"action": action}))
            self._log_llm_action_payload(
                response=llm_response,
                payload=payload,
                workflow=workflow,
                iteration=iteration,
                request_character_count=request_character_count,
            )
            if action == "final_report":
                if final_report is None:
                    raise RuntimeError("validated final_report payload is missing")
                final_report_allowed = not prompt.context.current_coverage.truncated_sources and (
                    prompt.context.final_report_allowed
                    or self._group_errors_cover_collection(
                        tool_results,
                        prompt.context.collection,
                    )
                )
                if not final_report_allowed:
                    force_strong = True
                    messages[2:] = [
                        self._build_final_report_not_allowed_message(
                            previous_action=payload,
                            tool_results=tool_results,
                        )
                    ]
                    continue
                if self.history_comparison_enabled:
                    correction_message: Message | None = (
                        self._build_history_comparison_claim_correction_message(
                            final_report=final_report,
                            prompt=prompt,
                            payload=payload,
                            workflow=workflow,
                            iteration=iteration,
                            tool_results=tool_results,
                        )
                    )
                    if correction_message is not None:
                        force_strong = True
                        messages[2:] = [correction_message]
                        continue
                if (
                    snapshot.omitted_details
                    and CONTEXT_LIMITATION not in final_report.coverage_gaps
                ):
                    final_report = final_report.model_copy(
                        update={"coverage_gaps": [*final_report.coverage_gaps, CONTEXT_LIMITATION]}
                    )
                return final_report, tool_results, llm_tokens_used, llm_cost_usd
            if model_tier is LogAnalysisModelTier.FAST:
                force_strong = True
            if action == "call_tools":
                if tool_request is None:
                    raise RuntimeError("validated tool request is missing")
                new_tool_results: list[LogAnalysisToolResult] = await self._execute_requested_tools(
                    tool_request=tool_request,
                    workflow=workflow,
                    executed_mcp_tool_calls=executed_mcp_tool_calls,
                    iteration=iteration,
                    analysis_date=analysis_date,
                    mcp_session_id=mcp_session_id,
                )
            elif action == "read_skills":
                if skill_request is None:
                    raise RuntimeError("validated skill request is missing")
                new_tool_results = await self._execute_requested_skill_reads(
                    skill_request=skill_request,
                    workflow=workflow,
                    fetched_skill_names=fetched_skill_names,
                    iteration=iteration,
                    analysis_date=analysis_date,
                    mcp_session_id=mcp_session_id,
                )
            tool_results.extend(new_tool_results)
            messages[2:] = [
                self._build_tool_loop_followup_message(
                    all_tool_results=tool_results,
                    fetched_skill_names=fetched_skill_names,
                    prompt=prompt,
                )
            ]

        raise ValueError("LLM tool loop exceeded maximum iterations before final_report.")

    @staticmethod
    def _build_invalid_fast_response_message(exc: Exception) -> Message:
        """Ask the strong model to repair an invalid fast-model action."""

        return Message.from_text(
            "user",
            json.dumps(
                {
                    "previous_fast_response_invalid": True,
                    "validation_error": str(exc),
                    "instruction": (
                        "Return one valid JSON action matching call_tools, read_skills, "
                        "or final_report. Use the retained deterministic evidence and "
                        "do not weaken coverage boundaries."
                    ),
                },
                sort_keys=True,
            ),
        )

    @staticmethod
    def _build_tool_loop_followup_message(
        *,
        all_tool_results: list[LogAnalysisToolResult],
        fetched_skill_names: set[str],
        prompt: LogAnalysisPreparedPrompt,
    ) -> Message:
        """Send a prompt-safe evidence ledger without repeating the initial prompt."""

        final_report_allowed = prompt.context.final_report_allowed or (
            MonitoringWorkflowAgent._group_errors_cover_collection(
                all_tool_results,
                prompt.context.collection,
            )
        )
        evidence_limitations: list[str] = [
            limitation
            for result in all_tool_results
            for limitation in _string_list(result.structured_content.get("limitations"))
        ]
        instruction: str = (
            "Use the retained initial prompt and critical rules. Interpret these exact "
            "results, request only material missing evidence, or return final_report."
        )
        if evidence_limitations:
            instruction += " Treat evidence_limitations as hard claim boundaries."
        payload: dict[str, object] = {
            "tool_results": [
                tool_result.model_dump(mode="json") for tool_result in all_tool_results
            ],
            "called_tool_names": sorted({result.tool_name for result in all_tool_results}),
            "optional_skill_status": {
                skill.name: ("retrieved" if skill.name in fetched_skill_names else "available")
                for skill in prompt.context.optional_skills
            },
            "current_tool_result_count": len(all_tool_results),
            "final_report_allowed": final_report_allowed,
            "next_required_action": (
                LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
                if final_report_allowed
                else LogAnalysisNextRequiredAction.CALL_TOOLS
            ),
            "instruction": instruction,
        }
        if evidence_limitations:
            payload["evidence_limitations"] = evidence_limitations
        return Message.from_text("user", json.dumps(payload, separators=(",", ":")))

    @staticmethod
    def _group_errors_cover_collection(
        tool_results: list[LogAnalysisToolResult],
        collection: LogAnalysisPromptCollection,
    ) -> bool:
        required = {
            project.project_name: {
                source.source_key
                for source in project.sources
                if source.status == LogSourceCollectionStatus.COLLECTED
            }
            for project in collection.projects
        }
        covered: dict[str, set[str]] = {}
        complete_result_found = False
        for result in tool_results:
            if (
                result.tool_name != McpToolName.GROUP_ERRORS
                or result.structured_content.get("truncated")
                or result.structured_content.get("partial_page")
            ):
                continue
            project_name = str(result.structured_content.get("project_name") or "")
            raw_source_keys = result.structured_content.get("searched_source_keys")
            if not project_name or not isinstance(raw_source_keys, list):
                continue
            complete_result_found = True
            source_keys = {str(value) for value in raw_source_keys if value}
            covered.setdefault(project_name, set()).update(source_keys)
        return (
            complete_result_found
            and any(required.values())
            and all(
                source_keys <= covered.get(project_name, set())
                for project_name, source_keys in required.items()
            )
        )

    @staticmethod
    def _build_final_report_not_allowed_message(
        *,
        previous_action: dict[str, object],
        tool_results: list[LogAnalysisToolResult],
    ) -> Message:
        """Build a correction message when current tool evidence is required first."""

        payload: dict[str, object] = {
            "previous_action": previous_action,
            "final_report_not_allowed_yet": True,
            "next_required_action": LogAnalysisNextRequiredAction.CALL_TOOLS,
            "final_report_allowed": False,
            "current_tool_result_count": len(tool_results),
            "tool_results": [tool_result.model_dump(mode="json") for tool_result in tool_results],
            "instruction": (
                "Call deterministic tools first. The prompt requires current MCP "
                "tool evidence before final_report because final_report_allowed=false "
                "and next_required_action=call_tools."
            ),
        }
        return Message.from_text("user", json.dumps(payload, separators=(",", ":")))

    def _build_history_comparison_claim_correction_message(
        self,
        *,
        final_report: LogAnalysisFinalReport,
        prompt: LogAnalysisPreparedPrompt,
        payload: dict[str, object],
        workflow: WorkflowBootstrap,
        iteration: int,
        tool_results: list[LogAnalysisToolResult],
    ) -> Message | None:
        """Return an LLM correction prompt for overbroad history-comparison claims."""

        history_comparison_evidence = prompt.context.evidence.get("history_comparison")
        if (
            not isinstance(history_comparison_evidence, dict)
            or history_comparison_evidence.get("status") != "available"
        ):
            return None

        history_comparison_service: LogAnalysisHistoryComparisonService | None = (
            self.history_comparison_service
        )
        if history_comparison_service is None:
            raise LogAnalysisHistoryComparisonServiceMissingException(
                "History comparison service is required when history comparison is enabled."
            )

        unsupported_claims: list[str] = (
            history_comparison_service.find_unsupported_history_comparison_claims(
                final_report=final_report,
                prompt_context=prompt.context,
            )
        )
        if not unsupported_claims:
            return None

        logger.warning(
            "rejecting LLM final report with unsupported history-comparison claims",
            extra={
                "event": "log_analysis_final_report_history_comparison_rejected",
                "workflow_name": workflow.workflow_name,
                "iteration": iteration,
                "unsupported_claim_count": len(unsupported_claims),
                "unsupported_claims": unsupported_claims,
            },
        )
        prompt_compacted_evidence = prompt.context.evidence.get("prompt_compacted")
        grouped_error_diff_payload = (
            prompt_compacted_evidence.get("grouped_error_diff")
            if isinstance(prompt_compacted_evidence, dict)
            else None
        )
        grouped_error_diff: LogAnalysisPromptGroupedErrorComparison | None = (
            LogAnalysisPromptGroupedErrorComparison.model_validate(grouped_error_diff_payload)
            if grouped_error_diff_payload is not None
            else None
        )
        current_grouped_error_scope_by_project: dict[str, list[str]] = (
            grouped_error_diff.current_tool_scope_by_project
            if grouped_error_diff is not None
            else {}
        )
        return Message.from_text(
            "user",
            json.dumps(
                {
                    "previous_action": payload,
                    "unsupported_history_comparison_claims": True,
                    "unsupported_claims": unsupported_claims,
                    "current_grouped_error_scope_by_project": (
                        current_grouped_error_scope_by_project
                    ),
                    "rejected_claim_repair_rules": [
                        "Do not claim overall service health or stable operation.",
                        "Do not claim no service impact.",
                        (
                            "Do not claim no upstream failures, no 5xx errors, or no "
                            "issues outside the grouped-error evidence scope."
                        ),
                        (
                            "Scope every current-run health statement to "
                            "current_grouped_error_scope_by_project or to inspected "
                            "grouped-error evidence."
                        ),
                        (
                            "Use inspected tool results as evidence only when the "
                            "specific tool result supports the specific claim."
                        ),
                    ],
                    "allowed_replacement_claim_examples": [
                        (
                            "No supported evidence of service-impacting grouped-error "
                            "changes was present inside the inspected grouped-error "
                            "scope."
                        ),
                        (
                            "Current grouped-error comparison covered only the listed "
                            "project/source keys; other sources were not reanalysed by "
                            "grouped-error evidence."
                        ),
                    ],
                    "forbidden_claim_examples": [
                        "real routes served normally",
                        "no service impact",
                        "no upstream errors",
                        "no 5xx errors",
                        "stable operation",
                        "TLS is healthy",
                    ],
                    "tool_results": [
                        tool_result.model_dump(mode="json") for tool_result in tool_results
                    ],
                    "instruction": (
                        "Return a corrected final_report. Keep current-run claims scoped "
                        "to current_grouped_error_scope_by_project. Do not claim stable "
                        "operation, no upstream failures, no 5xx errors, no service "
                        "impact, or no issues for projects/source_keys outside that "
                        "scope. You may cite previous_analysis only as historical context."
                    ),
                },
                indent=2,
            ),
        )

    def _request_llm_action(
        self,
        *,
        messages: list[Message],
        workflow: WorkflowBootstrap,
        analysis_date: date,
        iteration: int,
        llm_provider: LLMProvider,
        model_name: str | None,
        model_tier: LogAnalysisModelTier,
        route_reasons: tuple[str, ...],
    ) -> LLMResponse:
        """Ask the configured LLM provider for the next JSON workflow action."""

        option_values: dict[str, Any] = {
            "temperature": None,
            "max_output_tokens": self.max_output_tokens,
            "response_format": ResponseFormat.JSON_OBJECT,
        }
        option_fields: object = getattr(GenerationOptions, "__dataclass_fields__", {})
        if isinstance(option_fields, dict) and "reasoning_effort" in option_fields:
            option_values["reasoning_effort"] = (
                self.strong_reasoning_effort if model_tier is LogAnalysisModelTier.STRONG else None
            )
            option_values["text_verbosity"] = (
                self.text_verbosity if model_tier is LogAnalysisModelTier.STRONG else None
            )
        request_values: dict[str, Any] = {
            "messages": tuple(messages),
            "model": model_name,
            "options": GenerationOptions(**option_values),
            "metadata": {
                "workflow_name": workflow.workflow_name,
                "analysis_date": analysis_date.isoformat(),
                "phase": "log_analysis_2b",
                "iteration": str(iteration),
                "model_tier": model_tier.value,
                "model_route_reasons": ",".join(route_reasons),
            },
        }
        request_fields: object = getattr(LLMRequest, "__dataclass_fields__", {})
        if isinstance(request_fields, dict) and "prompt_cache_key" in request_fields:
            request_values["prompt_cache_key"] = "log-analysis:v1"
        request: LLMRequest = LLMRequest(**request_values)
        logger.info(
            "calling LLM for log-analysis workflow action",
            extra={
                "event": "log_analysis_llm_action_start",
                "workflow_name": workflow.workflow_name,
                "iteration": iteration,
                "provider": llm_provider.name,
                "model": model_name,
                "model_tier": model_tier.value,
                "model_route_reasons": list(route_reasons),
                "request_byte_count": message_bytes(messages),
                "request_character_count": self._message_character_count(messages),
                "message_character_counts": [
                    {
                        "role": message.role,
                        "character_count": self._message_character_count([message]),
                    }
                    for message in messages
                ],
            },
        )
        return llm_provider.generate(request)

    @staticmethod
    def _message_character_count(messages: list[Message]) -> int:
        """Return text characters sent in one provider request."""

        return sum(
            len(part.text)
            for message in messages
            for part in message.parts
            if isinstance(part, TextPart)
        )

    async def _record_llm_step(self, entry: LogAnalysisLLMCallIn) -> None:
        """Persist one LLM workflow step when DB recording is enabled."""

        if self.llm_call_repository is None:
            return
        await self.llm_call_repository.create(entry)

    @staticmethod
    def _log_llm_action_payload(
        *,
        response: LLMResponse,
        payload: dict[str, Any],
        workflow: WorkflowBootstrap,
        iteration: int,
        request_character_count: int,
    ) -> None:
        """Log the LLM action payload between tool-loop iterations."""

        action: object = payload.get("action")
        tool_calls: object = payload.get("tool_calls")
        skill_names: object = payload.get("skill_names")
        requested_tool_names: list[str] = []
        if isinstance(tool_calls, list):
            requested_tool_names = [
                str(tool_call["tool_name"])
                for tool_call in tool_calls
                if isinstance(tool_call, dict) and "tool_name" in tool_call
            ]
        extra: dict[str, Any] = {
            "event": "log_analysis_llm_action_received",
            "workflow_name": workflow.workflow_name,
            "iteration": iteration,
            "action": action,
            "requested_tool_names": requested_tool_names,
            "requested_skill_names": skill_names if isinstance(skill_names, list) else [],
            "tool_call_count": len(requested_tool_names),
            "llm_response_text": response.text,
            "llm_response_structured_output": response.structured_output,
            "llm_action_payload": payload,
            "provider_name": response.provider_name,
            "model_name": response.model_name,
            "request_character_count": request_character_count,
        }
        if response.usage is not None:
            extra.update(usage_telemetry(response.usage, model_name=response.model_name))
        if action == "final_report":
            key_findings: object = payload.get("key_findings")
            extra["final_report_severity"] = payload.get("severity")
            extra["final_report_key_finding_count"] = (
                len(key_findings) if isinstance(key_findings, list) else 0
            )
        logger.info("received LLM workflow action", extra=extra)

    async def _execute_requested_tools(
        self,
        *,
        tool_request: LogAnalysisToolCallRequest,
        workflow: WorkflowBootstrap,
        executed_mcp_tool_calls: set[str],
        iteration: int,
        analysis_date: date,
        mcp_session_id: str | None,
    ) -> list[LogAnalysisToolResult]:
        """Execute validated MCP tools requested by the LLM action."""

        available_tool_names: set[str] = {tool.tool_name for tool in workflow.tools}
        tool_results: list[LogAnalysisToolResult] = []
        if not tool_request.tool_calls:
            raise ValueError("LLM call_tools action did not include any tool calls.")
        for tool_call in tool_request.tool_calls:
            if tool_call.tool_name not in available_tool_names:
                raise ValueError(f"LLM requested unavailable MCP tool: {tool_call.tool_name}")
            tool_call_key: str = self._build_mcp_tool_call_key(tool_call)
            if tool_call_key in executed_mcp_tool_calls:
                logger.info(
                    "skipping duplicate LLM-requested MCP tool call",
                    extra={
                        "event": "log_analysis_duplicate_mcp_tool_call_skipped",
                        "tool_name": tool_call.tool_name,
                    },
                )
                await self._record_llm_step(
                    _build_tool_call_entry(
                        analysis_date=analysis_date,
                        workflow_name=workflow.workflow_name,
                        mcp_session_id=mcp_session_id,
                        iteration=iteration,
                        tool_name=tool_call.tool_name,
                        arguments=tool_call.arguments,
                        step_type="mcp_tool_call",
                        status="skipped",
                        duplicate_skipped=True,
                        result_summary="Duplicate LLM-requested MCP tool call skipped.",
                    )
                )
                tool_results.append(
                    LogAnalysisToolResult(
                        tool_name="duplicate_mcp_tool_call_skipped",
                        arguments=tool_call.arguments,
                        structured_content={
                            "action": "duplicate_mcp_tool_call_skipped",
                            "tool_name": tool_call.tool_name,
                            "message": (
                                "This MCP tool call was already executed with the same "
                                "arguments. Use the previous result, request a different "
                                "tool, or return final_report."
                            ),
                        },
                    )
                )
                continue
            executed_mcp_tool_calls.add(tool_call_key)
            tool_started_at = datetime.now(UTC)
            tool_started_monotonic = monotonic()
            try:
                if tool_call.tool_name == McpToolName.GROUP_ERRORS:
                    group_errors_arguments = GroupErrorsArgumentsModel.model_validate(
                        tool_call.arguments
                    )
                    group_errors_response: GroupErrorsResponseModel = (
                        await self._collect_all_group_error_pages(
                            arguments=group_errors_arguments,
                        )
                    )
                    structured_content: dict[str, Any] = group_errors_response.model_dump(
                        mode="json",
                        exclude_unset=True,
                    )
                else:
                    deterministic_response: DeterministicToolResponseModel = (
                        await self.mcp_client.call_deterministic_tool(
                            tool_call.tool_name,
                            arguments=tool_call.arguments,
                            response_model=DeterministicToolResponseModel,
                        )
                    )
                    structured_content = deterministic_response.model_dump(mode="json")
            except McpClientError as exc:
                await self._record_llm_step(
                    _build_tool_call_entry(
                        analysis_date=analysis_date,
                        workflow_name=workflow.workflow_name,
                        mcp_session_id=mcp_session_id,
                        iteration=iteration,
                        tool_name=tool_call.tool_name,
                        arguments=tool_call.arguments,
                        step_type="mcp_tool_call",
                        status="failed",
                        started_at=tool_started_at,
                        finished_at=datetime.now(UTC),
                        duration_ms=elapsed_ms(tool_started_monotonic),
                        error_message=str(exc),
                    )
                )
                raise
            await self._record_llm_step(
                _build_tool_call_entry(
                    analysis_date=analysis_date,
                    workflow_name=workflow.workflow_name,
                    mcp_session_id=mcp_session_id,
                    iteration=iteration,
                    tool_name=tool_call.tool_name,
                    arguments=tool_call.arguments,
                    step_type="mcp_tool_call",
                    status="succeeded",
                    started_at=tool_started_at,
                    finished_at=datetime.now(UTC),
                    duration_ms=elapsed_ms(tool_started_monotonic),
                    result_summary=str(structured_content.get("action", "")),
                )
            )
            tool_results.append(
                LogAnalysisToolResult(
                    tool_name=tool_call.tool_name,
                    arguments=tool_call.arguments,
                    structured_content=structured_content,
                )
            )
        return tool_results

    @staticmethod
    def _build_mcp_tool_call_key(tool_call: LogAnalysisToolCall) -> str:
        """Return a stable key for one MCP tool name plus its arguments."""

        arguments: dict[str, Any] = dict(tool_call.arguments)
        if tool_call.tool_name == McpToolName.GROUP_ERRORS:
            arguments = _normalized_group_errors_arguments_for_dedup(arguments)
        return json.dumps(
            {
                "tool_name": tool_call.tool_name,
                "arguments": arguments,
            },
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _build_current_coverage(
        collect_logs: CollectLogsArtifact,
    ) -> LogAnalysisCurrentCoverage:
        """Build current source coverage state facts the LLM may cite in coverage gaps."""

        collection_warnings: list[str] = sorted(
            {
                warning
                for project in collect_logs.projects
                for warning in project.warnings
                if warning
            }
        )
        unknown_requested_sources: list[str] = sorted(
            {
                f"{project.project_name}.{source_key}"
                for project in collect_logs.projects
                for source_key in project.unknown_requested_source_keys
                if source_key
            }
        )
        zero_line_sources: list[str] = []
        unavailable_sources: list[str] = []
        truncated_sources: list[str] = []
        continuation_available_sources: list[str] = []
        for project in collect_logs.projects:
            project_name: str = project.project_name
            for source in project.sources:
                source_key: str = source.source_key
                source_name: str = f"{project_name}.{source_key}"
                if source.status == LogSourceCollectionStatus.UNAVAILABLE:
                    unavailable_sources.append(source_name)
                elif source.line_count == 0:
                    zero_line_sources.append(source_name)
                if source.transfer is not None and source.transfer.truncated:
                    truncated_sources.append(source_name)
                if source.transfer is not None and source.transfer.next_offset is not None:
                    continuation_available_sources.append(source_name)
        return LogAnalysisCurrentCoverage(
            collection_warnings=collection_warnings,
            unknown_requested_sources=unknown_requested_sources,
            zero_line_sources=zero_line_sources,
            unavailable_sources=unavailable_sources,
            truncated_sources=truncated_sources,
            continuation_available_sources=continuation_available_sources,
        )

    @staticmethod
    def _build_prompt_collection(
        collect_logs: CollectLogsArtifact,
    ) -> LogAnalysisPromptCollection:
        """Build a compact collect_logs view for the LLM prompt."""

        return LogAnalysisPromptCollection(
            action=collect_logs.action,
            workspace=collect_logs.workspace,
            session_id=collect_logs.session_id,
            projects=[
                LogAnalysisPromptCollectedProject(
                    project_name=project.project_name,
                    snapshot_dir=project.snapshot_dir,
                    resolved_source_keys=project.resolved_source_keys,
                    sources=[
                        LogAnalysisPromptCollectedSource(
                            source_key=source.source_key,
                            status=source.status,
                            line_count=source.line_count,
                            zero_lines=source.line_count == 0,
                            truncated=(
                                source.transfer.truncated if source.transfer is not None else False
                            ),
                            continuation_available=(
                                source.transfer.next_offset is not None
                                if source.transfer is not None
                                else False
                            ),
                        )
                        for source in project.sources
                    ],
                )
                for project in collect_logs.projects
            ],
        )

    async def _execute_requested_skill_reads(
        self,
        *,
        skill_request: LogAnalysisSkillReadRequest,
        workflow: WorkflowBootstrap,
        fetched_skill_names: set[str],
        iteration: int,
        analysis_date: date,
        mcp_session_id: str | None,
    ) -> list[LogAnalysisToolResult]:
        """Read optional MCP workflow skill resources requested by the LLM action."""

        optional_skills_by_name: dict[str, WorkflowSkill] = {
            skill.name: skill for skill in workflow.optional_skills
        }
        if not skill_request.skill_names:
            raise ValueError("LLM read_skills action did not include any skill names.")

        skill_contents: list[dict[str, str]] = []
        for skill_name in skill_request.skill_names:
            skill: WorkflowSkill | None = optional_skills_by_name.get(skill_name)
            if skill is None:
                raise ValueError(f"LLM requested unavailable optional skill: {skill_name}")
            if skill.name in fetched_skill_names:
                raise ValueError(f"LLM requested already fetched optional skill: {skill.name}")
            content: str = await self.mcp_client.read_resource(skill.resource_uri)
            fetched_skill_names.add(skill.name)
            await self._record_llm_step(
                LogAnalysisLLMCallIn(
                    analysis_date=analysis_date,
                    workflow_name=workflow.workflow_name,
                    mcp_session_id=mcp_session_id,
                    iteration=iteration,
                    step_type="skill_read",
                    action="read_skills",
                    skill_name=skill.name,
                    status="succeeded",
                    result_summary=skill.resource_uri,
                )
            )
            skill_contents.append(
                {
                    "skill_name": skill.name,
                    "resource_uri": skill.resource_uri,
                    "description": skill.description,
                    "content": content,
                }
            )

        return [
            LogAnalysisToolResult(
                tool_name="read_skills",
                arguments={"skill_names": skill_request.skill_names},
                structured_content={
                    "action": "read_skills",
                    "skills": skill_contents,
                },
            )
        ]

    @staticmethod
    def _extract_llm_payload(response: LLMResponse) -> dict[str, Any]:
        """Extract a JSON object payload from an LLM response."""

        payload: Any = response.structured_output
        if payload is None and response.text is not None:
            try:
                payload = json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise ValueError("LLM action response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("LLM action response must be a JSON object.")
        return payload

    @staticmethod
    def _build_tool_call_request(payload: dict[str, Any]) -> LogAnalysisToolCallRequest:
        """Validate an LLM call_tools action."""

        try:
            return LogAnalysisToolCallRequest.model_validate(payload)
        except (TypeError, ValidationError, StructuredOutputError) as exc:
            raise ValueError("LLM tool request did not match expected shape.") from exc

    @staticmethod
    def _build_skill_read_request(payload: dict[str, Any]) -> LogAnalysisSkillReadRequest:
        """Validate an LLM read_skills action."""

        try:
            return LogAnalysisSkillReadRequest.model_validate(payload)
        except (TypeError, ValidationError, StructuredOutputError) as exc:
            raise ValueError("LLM skill read request did not match expected shape.") from exc

    @staticmethod
    def _build_final_report_payload(payload: dict[str, Any]) -> LogAnalysisFinalReport:
        """Validate a final report payload returned by the LLM provider."""

        try:
            return LogAnalysisFinalReport.model_validate(payload)
        except (TypeError, ValidationError, StructuredOutputError) as exc:
            raise ValueError("LLM final report did not match expected shape.") from exc


def _build_tool_call_entry(
    *,
    analysis_date: date | None,
    workflow_name: str | None,
    mcp_session_id: str | None,
    iteration: int | None,
    tool_name: str,
    arguments: dict[str, Any],
    step_type: str,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    duplicate_skipped: bool = False,
    error_message: str = "",
    result_summary: str = "",
) -> LogAnalysisLLMCallIn:
    arguments_text = dump_arguments(arguments)
    return LogAnalysisLLMCallIn(
        analysis_date=analysis_date,
        workflow_name=workflow_name,
        mcp_session_id=mcp_session_id,
        iteration=iteration,
        step_type=step_type,
        tool_name=tool_name,
        arguments_hash=hash_text(arguments_text),
        arguments_text=arguments_text,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        duplicate_skipped=duplicate_skipped,
        error_message=error_message,
        result_summary=result_summary,
    )


def _build_trend_summary_instruction(*, historical_context_available: bool) -> str:
    if historical_context_available:
        return (
            "Historical context was provided in the system prompt. Compare current "
            "tool results against it and do not claim no historical data was provided."
        )
    return (
        "No historical context was provided. State that no historical trend data "
        "was available for comparison."
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item)]


def _int_or_zero(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    try:
        return int(str(value))
    except ValueError:
        return 0


def _normalized_group_errors_arguments_for_dedup(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return group_errors arguments normalized for same-scope duplicate detection."""

    normalized: dict[str, Any] = {
        key: value
        for key, value in arguments.items()
        if key not in {"max_groups", "limit", "offset", "include_examples"}
    }
    source_keys: set[str] = set()
    source_key: object = arguments.get("source_key")
    if isinstance(source_key, str) and source_key:
        source_keys.add(source_key)
        normalized.pop("source_key", None)
    raw_source_keys: object = arguments.get("source_keys")
    if isinstance(raw_source_keys, list):
        source_keys.update(str(value) for value in raw_source_keys if value)
        normalized.pop("source_keys", None)
    if source_keys:
        normalized["source_keys"] = sorted(source_keys)
    return normalized
