from __future__ import annotations

import json
from datetime import UTC, date, datetime
from time import monotonic
from typing import Any

from llm_core.exceptions import StructuredOutputError
from llm_core.protocols import LLMProvider
from llm_core.types import GenerationOptions, LLMRequest, LLMResponse, Message, ResponseFormat
from pydantic import ValidationError

from assets_loader import load_json, load_markdown_bullets, load_markdown_mapping, load_text
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
)
from services.log_fingerprints import LogAnalysisFingerprintBuilder, build_grouped_error_run
from services.log_history_comparison import LogAnalysisHistoryComparisonService
from utils.llm_usage import usage_cost_usd
from utils.runtime import dump_arguments, elapsed_ms, hash_text

logger = get_logger(__name__)
MAX_LLM_TOOL_LOOP_ITERATIONS = 5
LOG_ANALYSIS_INSTRUCTIONS = load_markdown_bullets("log_analysis_instructions.md")
LOG_ANALYSIS_FOLLOWUP_INSTRUCTIONS = load_markdown_bullets("log_analysis_followup_instructions.md")
LOG_ANALYSIS_REPORT_CONTRACT = load_markdown_mapping("log_analysis_report_contract.md")
LOG_ANALYSIS_DECISION_SKILL = load_text("log_analysis_decision_skill.md")
LOG_ANALYSIS_CRITICAL_DECISION_RULES = load_text("log_analysis_critical_decision_rules.md")
HISTORICAL_CONTEXT_TEMPLATE = load_text("historical_context.md")
LOG_ANALYSIS_NO_COMPARE_HISTORY_PROMPT = load_json("log_analysis_no_compare_history_prompt.json")
LOG_ANALYSIS_COMPARE_HISTORY_PROMPT = load_json("log_analysis_compare_history_prompt.json")


class MonitoringWorkflowAgent:
    """Agent boundary for MCP-backed monitoring workflow bootstrap calls."""

    def __init__(
        self,
        mcp_client: McpWorkflowClient,
        llm_provider: LLMProvider,
        private_monitoring_context: str,
        history_comparison_service: LogAnalysisHistoryComparisonService | None = None,
        history_comparison_enabled: bool = False,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm_provider = llm_provider
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
        """Prepare deterministic context before the first log-analysis LLM call."""

        logger.info(
            "loading MCP daily log workflow bundle",
            extra={"event": "workflow_bundle_load_start"},
        )
        workflow: WorkflowBootstrap = await self.mcp_client.get_workflow_bundle()
        mandatory_skills: list[WorkflowSkillContent] = await self._read_mandatory_skills(
            workflow.mandatory_skills
        )
        available_projects: list[ProjectManifestSummary] = await self.mcp_client.list_projects()
        if not available_projects:
            raise McpClientError(
                (
                    "MCP list_projects returned no projects for this workflow caller. "
                    "Upload project manifests to MCP or check the caller project scope "
                    "before collecting logs."
                ),
                mcp_url=self.mcp_client.base_url,
                tool_name=McpToolName.LIST_PROJECTS,
            )
        current_logs: CollectLogsArtifact = await self.mcp_client.collect_logs(
            since=log_window.since,
            until=log_window.until,
        )
        previous_analysis_context: PreviousLogAnalysisContext | None = (
            PreviousLogAnalysisContext.from_analysis(previous_analysis)
            if previous_analysis is not None
            else None
        )
        current_grouped_errors: list[LogAnalysisGroupedErrorRunFingerprint] = (
            await self._collect_current_grouped_errors(current_logs=current_logs)
        )
        current_coverage_snapshot: dict[str, Any] = (
            LogAnalysisFingerprintBuilder.build_coverage_snapshot(current_logs)
        )
        prepared_evidence: LogAnalysisPromptEvidence = self._prepare_log_analysis_evidence(
            current_grouped_errors=current_grouped_errors,
            current_coverage_snapshot=current_coverage_snapshot,
            previous_analysis=previous_analysis_context,
        )
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
        llm_report_started_at: float = monotonic()
        final_report: LogAnalysisFinalReport
        tool_results: list[LogAnalysisToolResult]
        llm_tokens_used: int
        llm_cost_usd: float
        try:
            final_report, tool_results, llm_tokens_used, llm_cost_usd = await self._run_tool_loop(
                prompt=prompt,
                workflow=workflow,
                analysis_date=analysis_date,
                mcp_session_id=current_logs.session_id,
            )
        except Exception as exc:
            raise LogAnalysisAgentError(
                str(exc),
                workflow=workflow,
                collect_logs=current_logs,
                prompt=prompt,
            ) from exc
        llm_report_execution_time_seconds: float = round(monotonic() - llm_report_started_at, 3)
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
                "llm_report_execution_time_seconds": llm_report_execution_time_seconds,
            },
        )
        return LogAnalysisAgentContext(
            workflow=workflow,
            collect_logs=current_logs,
            prompt=prompt,
            tool_results=tool_results,
            final_report=final_report,
            log_window_since=log_window.since_datetime,
            log_window_until=log_window.until_datetime,
            llm_tokens_used=llm_tokens_used,
            llm_cost_usd=llm_cost_usd,
            llm_report_execution_time_seconds=llm_report_execution_time_seconds,
        )

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
            )
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
            )

        previous_groups: list[LogAnalysisGroupedErrorSignal] = (
            self._extract_grouped_error_signals_from_previous_analysis(previous_analysis)
        )
        previous_grouped_error_evidence = self._compact_grouped_error_baseline_for_prompt(
            label=LogAnalysisGroupedErrorEvidenceLabel.PREVIOUS,
            groups=previous_groups,
            run_count=len(previous_analysis.fingerprints.grouped_error_runs),
            tool_scope_by_project=self._build_previous_grouped_error_tool_scope_by_project(
                previous_analysis
            ),
            rationale=(
                "Previous grouped-error fingerprints from the stored log-analysis DB object. "
                "Use as historical baseline evidence, not as current log evidence."
            ),
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

        grouped_error_comparison: LogAnalysisGroupedErrorComparison | None = (
            self.history_comparison_service.compare_grouped_errors(  # type: ignore[union-attr]
                previous_grouped_errors=previous_analysis.fingerprints.grouped_error_runs,
                current_grouped_errors=current_grouped_errors,
            )
        )
        source_coverage_comparison: LogAnalysisSourceCoverageComparison = (
            self.history_comparison_service.build_missing_source_comparison(  # type: ignore[union-attr]
                previous_coverage_snapshot=previous_analysis.coverage_snapshot.model_dump(
                    mode="json"
                ),
                current_coverage_snapshot=current_coverage_snapshot,
                previous_severity=previous_analysis.severity,
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
                        "Current grouped-error evidence is already available. Treat "
                        "source coverage changes and previous severity as comparison "
                        "context; let the LLM decide whether more tools are needed."
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
        current_grouped_evidence_available: bool = prepared_evidence.kind in {
            LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON,
            LogAnalysisPromptEvidenceKind.GROUPED_ERROR_BASELINE,
        }
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
        if current_grouped_evidence_available or (
            previous_analysis_context is not None
            and prepared_evidence.kind != LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON
        ):
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
                final_report_allowed=(
                    current_grouped_evidence_available
                    or (
                        prepared_evidence.kind != LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON
                        and previous_analysis_context is not None
                    )
                ),
                available_projects=available_projects,
                mandatory_skills=[
                    WorkflowSkill(
                        skill_name=skill.name,
                        resource_uri=skill.resource_uri,
                        description=skill.description,
                        when_useful="Already loaded into the system prompt.",
                    )
                    for skill in mandatory_skills
                ],
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
                available_tools=workflow.tools,
                report_contract=LOG_ANALYSIS_REPORT_CONTRACT,
                instructions=self._build_mode_specific_log_analysis_instructions(
                    history_comparison_enabled=(
                        prepared_evidence.kind == LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON
                    )
                ),
            ),
        )

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
    ) -> LogAnalysisPromptEvidence:
        """Build prompt evidence for deterministic history-comparison mode."""

        return LogAnalysisPromptEvidence(
            kind=LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON,
            decision_prompt=LOG_ANALYSIS_COMPARE_HISTORY_PROMPT,
            history_comparison=LogAnalysisPromptHistoryComparisonState(
                status=history_comparison_status,
            ),
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
    def _build_previous_grouped_error_tool_scope_by_project(
        previous_analysis: PreviousLogAnalysisContext,
    ) -> dict[str, list[str]]:
        """Return group_errors project/source scope stored in the previous DB analysis."""

        tool_scope_by_project: dict[str, set[str]] = {}
        for run in previous_analysis.fingerprints.grouped_error_runs:
            project_name: object = run.arguments.get("project_name")
            if not isinstance(project_name, str) or not project_name:
                continue
            raw_source_keys: object = run.arguments.get("source_keys")
            if isinstance(raw_source_keys, list):
                source_keys = {str(source_key) for source_key in raw_source_keys if source_key}
            else:
                source_keys = set()
            tool_scope_by_project.setdefault(project_name, set()).update(source_keys)
        return {
            project: sorted(source_keys)
            for project, source_keys in sorted(tool_scope_by_project.items())
        }

    @staticmethod
    def _compact_grouped_error_baseline_for_prompt(
        *,
        label: LogAnalysisGroupedErrorEvidenceLabel,
        groups: list[LogAnalysisGroupedErrorSignal],
        run_count: int,
        tool_scope_by_project: dict[str, list[str]],
        rationale: str,
    ) -> LogAnalysisPromptGroupedErrorEvidence | None:
        """Return the shared compact baseline shape for previous or current grouped errors."""

        if run_count == 0 and not groups:
            return None
        severity_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        status_code_counts: dict[str, int] = {}
        source_key_counts: dict[str, int] = {}
        for group in groups:
            severity: str = group.severity or "unknown"
            category: str = group.category or "unknown"
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1
            for status_code in group.status_codes:
                status_code_key: str = str(status_code)
                status_code_counts[status_code_key] = status_code_counts.get(status_code_key, 0) + 1
            for source_key in group.source_keys:
                source_key_counts[source_key] = source_key_counts.get(source_key, 0) + 1
        return LogAnalysisPromptGroupedErrorEvidence(
            available=True,
            label=label,
            tool_scope_by_project=tool_scope_by_project,
            run_count=run_count,
            group_count=len(groups),
            severity_counts=severity_counts,
            category_counts=category_counts,
            status_code_counts=status_code_counts,
            source_key_counts=source_key_counts,
            fingerprints=[
                MonitoringWorkflowAgent._compact_grouped_error_fingerprint(signal)
                for signal in groups
            ],
            rationale=rationale,
        )

    @staticmethod
    def _compact_grouped_error_fingerprint(
        signal: LogAnalysisGroupedErrorSignal,
    ) -> LogAnalysisPromptGroupedErrorFingerprint:
        """Keep only stable fingerprint identity fields for prompt baselines."""

        return LogAnalysisPromptGroupedErrorFingerprint(
            fingerprint=signal.fingerprint,
            project_name=signal.project_name,
            category=signal.category,
            severity=signal.severity,
            source_keys=signal.source_keys,
            status_codes=signal.status_codes,
        )

    @staticmethod
    def _build_grouped_error_tool_scope_by_project(
        current_tool_results: list[LogAnalysisToolResult],
    ) -> dict[str, list[str]]:
        """Return the project/source scope covered by `group_errors` tool calls."""

        source_keys_by_project: dict[str, set[str]] = {}
        for tool_result in current_tool_results:
            if tool_result.tool_name != McpToolName.GROUP_ERRORS:
                continue
            project_name: str = str(tool_result.arguments.get("project_name") or "")
            if not project_name:
                continue
            raw_source_keys: object = tool_result.arguments.get("source_keys")
            raw_source_key: object = tool_result.arguments.get("source_key")
            if isinstance(raw_source_keys, list):
                source_keys = [str(source_key) for source_key in raw_source_keys if source_key]
            elif raw_source_key:
                source_keys = [str(raw_source_key)]
            else:
                source_keys = []
            source_keys_by_project.setdefault(project_name, set()).update(source_keys)
        return {
            project_name: sorted(source_keys)
            for project_name, source_keys in sorted(source_keys_by_project.items())
        }

    @staticmethod
    def _build_group_errors_arguments_from_current_logs(
        current_logs: CollectLogsArtifact,
    ) -> list[dict[str, Any]]:
        """Build scoped `group_errors` arguments from the current log collection.

        `collect_logs` tells us which projects and source keys were actually
        resolved for this run, while `group_errors` expects explicit MCP
        arguments. This bridge keeps the pre-LLM grouped-error baseline scoped
        to observed sources instead of asking for broad/all-project analysis or
        guessing source names from previous history.
        """

        arguments_list: list[dict[str, Any]] = []
        for project in sorted(current_logs.projects, key=lambda item: item.project_name):
            project_name: str = project.project_name
            if not project_name:
                continue
            source_keys: list[str] = sorted(
                {source.source_key for source in project.sources if source.source_key}
            )
            arguments: dict[str, Any] = {"project_name": project_name}
            if source_keys:
                arguments["source_keys"] = source_keys
            arguments_list.append(arguments)
        return arguments_list

    async def _collect_current_grouped_errors(
        self,
        *,
        current_logs: CollectLogsArtifact,
    ) -> list[LogAnalysisGroupedErrorRunFingerprint]:
        """Collect current grouped-error evidence before the LLM decision."""

        grouped_errors: list[LogAnalysisGroupedErrorRunFingerprint] = []
        group_errors_arguments: list[dict[str, Any]] = (
            self._build_group_errors_arguments_from_current_logs(current_logs)
        )
        for arguments in group_errors_arguments:
            structured_content: dict[str, Any] = await self.mcp_client.call_deterministic_tool(
                McpToolName.GROUP_ERRORS,
                arguments,
            )
            grouped_errors.append(
                build_grouped_error_run(
                    arguments=arguments,
                    structured_content=structured_content,
                )
            )
        return grouped_errors

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
                LOG_ANALYSIS_DECISION_SKILL.strip(),
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
    ) -> tuple[LogAnalysisFinalReport, list[LogAnalysisToolResult], int, float]:
        """Run the LLM action loop until a final report is produced."""

        messages: list[Message] = [
            Message.from_text("system", prompt.system_prompt),
            Message.from_text("user", prompt.user_prompt),
        ]
        tool_results: list[LogAnalysisToolResult] = []
        fetched_skill_names: set[str] = set()
        executed_mcp_tool_calls: set[str] = set()
        llm_tokens_used: int = 0
        llm_cost_usd: float = 0.0
        for iteration in range(1, MAX_LLM_TOOL_LOOP_ITERATIONS + 1):
            llm_response: LLMResponse = self._request_llm_action(
                messages=messages,
                workflow=workflow,
                analysis_date=analysis_date,
                iteration=iteration,
            )
            if llm_response.usage is not None:
                llm_tokens_used += llm_response.usage.total_tokens
                llm_cost_usd += usage_cost_usd(llm_response.usage)

            payload: dict[str, Any] = self._extract_llm_payload(llm_response)
            action: object = payload.get("action")
            await self._record_llm_step(
                LogAnalysisLLMCallIn(
                    analysis_date=analysis_date,
                    workflow_name=workflow.workflow_name,
                    mcp_session_id=mcp_session_id,
                    iteration=iteration,
                    step_type="llm_call",
                    action=str(action or ""),
                    llm_response_text=llm_response.text or "",
                )
            )
            self._log_llm_action_payload(
                response=llm_response,
                payload=payload,
                workflow=workflow,
                iteration=iteration,
            )
            if action == "final_report":
                final_report: LogAnalysisFinalReport = self._build_final_report_payload(payload)
                if (
                    not prompt.context.final_report_allowed
                    and prompt.context.next_required_action
                    == LogAnalysisNextRequiredAction.CALL_TOOLS
                    and not tool_results
                ):
                    messages.append(
                        self._build_final_report_not_allowed_message(
                            previous_action=payload,
                            prompt=prompt,
                        )
                    )
                    continue
                if self.history_comparison_enabled:
                    correction_message: Message | None = (
                        self._build_history_comparison_claim_correction_message(
                            final_report=final_report,
                            prompt=prompt,
                            payload=payload,
                            workflow=workflow,
                            iteration=iteration,
                        )
                    )
                    if correction_message is not None:
                        messages.append(correction_message)
                        continue
                return final_report, tool_results, llm_tokens_used, llm_cost_usd
            if action == "call_tools":
                tool_request: LogAnalysisToolCallRequest = self._build_tool_call_request(payload)
                new_tool_results: list[LogAnalysisToolResult] = await self._execute_requested_tools(
                    tool_request=tool_request,
                    workflow=workflow,
                    executed_mcp_tool_calls=executed_mcp_tool_calls,
                    iteration=iteration,
                    analysis_date=analysis_date,
                    mcp_session_id=mcp_session_id,
                )
            elif action == "read_skills":
                skill_request: LogAnalysisSkillReadRequest = self._build_skill_read_request(payload)
                new_tool_results = await self._execute_requested_skill_reads(
                    skill_request=skill_request,
                    workflow=workflow,
                    fetched_skill_names=fetched_skill_names,
                    iteration=iteration,
                    analysis_date=analysis_date,
                    mcp_session_id=mcp_session_id,
                )
            else:
                raise ValueError("LLM action did not match expected shape.")

            tool_results.extend(new_tool_results)
            messages.append(
                self._build_tool_loop_followup_message(
                    previous_action=payload,
                    new_tool_results=new_tool_results,
                    all_tool_results=tool_results,
                    workflow=workflow,
                    prompt=prompt,
                    fetched_skill_names=fetched_skill_names,
                )
            )

        raise ValueError("LLM tool loop exceeded maximum iterations before final_report.")

    @staticmethod
    def _build_tool_loop_followup_message(
        *,
        previous_action: dict[str, object],
        new_tool_results: list[LogAnalysisToolResult],
        all_tool_results: list[LogAnalysisToolResult],
        workflow: WorkflowBootstrap,
        prompt: LogAnalysisPreparedPrompt,
        fetched_skill_names: set[str],
    ) -> Message:
        """Build the next LLM message after tools or skills were executed."""

        called_tool_names: set[str] = {result.tool_name for result in all_tool_results}
        current_evidence_available: bool = bool(all_tool_results)
        payload: dict[str, object] = {
            "previous_action": previous_action,
            "tool_results": [
                MonitoringWorkflowAgent._compact_tool_result_for_prompt(tool_result)
                for tool_result in new_tool_results
            ],
            "available_tool_status": [
                {
                    "tool_name": tool.tool_name,
                    "already_called": tool.tool_name in called_tool_names,
                }
                for tool in workflow.tools
            ],
            "optional_skill_status": [
                {
                    "skill_name": skill.name,
                    "already_retrieved": skill.name in fetched_skill_names,
                }
                for skill in workflow.optional_skills
            ],
            "initial_context_reference": {
                "instruction": (
                    "Use the initial prompt already present earlier in this conversation for "
                    "previous_analysis, evidence, current_coverage, full tool "
                    "inventory, report_contract, and mandatory instructions. This follow-up "
                    "contains only new evidence and small status updates to avoid repeating "
                    "large prompt context."
                ),
                "historical_context_available": prompt.context.historical_context_available,
                "previous_analysis_available": prompt.context.previous_analysis is not None,
                "history_comparison_status": (
                    prompt.context.evidence.get("history_comparison", {}).get("status")
                    if isinstance(prompt.context.evidence.get("history_comparison"), dict)
                    else None
                ),
                "history_comparison_has_grouped_error_diff": (
                    isinstance(prompt.context.evidence.get("prompt_compacted"), dict)
                    and prompt.context.evidence.get("prompt_compacted", {}).get(
                        "grouped_error_diff"
                    )
                    is not None
                ),
                "current_coverage_available": True,
            },
            "evidence_mode": (
                "current_tool_results_available"
                if current_evidence_available
                else prompt.context.evidence_mode
            ),
            "current_tool_result_count": len(all_tool_results),
            "next_required_action": (
                "choose_next_action"
                if current_evidence_available
                else prompt.context.next_required_action
            ),
            "final_report_allowed": (
                True if current_evidence_available else prompt.context.final_report_allowed
            ),
            "trend_summary_instruction": prompt.context.trend_summary_instruction,
            "instructions": MonitoringWorkflowAgent._build_mode_specific_followup_instructions(
                prompt
            ),
        }
        return Message.from_text("user", json.dumps(payload, separators=(",", ":")))

    @staticmethod
    def _build_mode_specific_followup_instructions(
        prompt: LogAnalysisPreparedPrompt,
    ) -> list[str]:
        """Return follow-up instructions matching the initial evidence mode."""

        evidence_kind = prompt.context.evidence.get("kind")
        excluded_markers: tuple[str, ...]
        if evidence_kind == "history_comparison":
            excluded_markers = (
                "history_baseline",
                "grouped_error_baseline",
                "disabled-history",
                "previous_grouped_errors and current_grouped_errors",
            )
        else:
            excluded_markers = (
                "history_comparison.",
                "history_comparison.status",
                "grouped_error_diff",
                "compare-history",
                "deterministic history comparison",
            )
        return [
            instruction
            for instruction in LOG_ANALYSIS_FOLLOWUP_INSTRUCTIONS
            if not any(marker in instruction for marker in excluded_markers)
        ]

    @staticmethod
    def _compact_tool_result_for_prompt(tool_result: LogAnalysisToolResult) -> dict[str, object]:
        """Return a bounded prompt-facing view of one deterministic tool result."""

        dumped: dict[str, object] = tool_result.model_dump(mode="json")
        if tool_result.tool_name == McpToolName.GROUP_ERRORS:
            dumped["structured_content"] = (
                MonitoringWorkflowAgent._compact_group_errors_result_for_prompt(
                    tool_result.structured_content
                )
            )
        elif tool_result.tool_name == McpToolName.GREP_LOG_SNAPSHOT:
            dumped["structured_content"] = (
                MonitoringWorkflowAgent._compact_grep_log_snapshot_result_for_prompt(
                    tool_result.structured_content
                )
            )
        return dumped

    @staticmethod
    def _compact_group_errors_result_for_prompt(
        structured_content: dict[str, Any],
        *,
        max_groups: int = 20,
    ) -> dict[str, object]:
        """Compact MCP group_errors output while preserving decision facts."""

        groups: object = structured_content.get("groups", [])
        if not isinstance(groups, list):
            return dict(structured_content)

        compacted_groups: list[dict[str, object]] = [
            MonitoringWorkflowAgent._compact_group_error_for_prompt(group)
            for group in groups[:max_groups]
            if isinstance(group, dict)
        ]
        severity_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        status_code_counts: dict[str, int] = {}
        source_key_counts: dict[str, int] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            severity: str = str(group.get("severity") or "unknown")
            category: str = str(group.get("category") or "unknown")
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1
            for status_code in group.get("status_codes") or []:
                status_code_key: str = str(status_code)
                status_code_counts[status_code_key] = status_code_counts.get(status_code_key, 0) + 1
            for source_key in group.get("source_keys") or []:
                source_key_name: str = str(source_key)
                source_key_counts[source_key_name] = source_key_counts.get(source_key_name, 0) + 1

        compacted: dict[str, object] = {
            key: value for key, value in structured_content.items() if key not in {"groups"}
        }
        compacted.update(
            {
                "prompt_compacted": True,
                "groups": compacted_groups,
                "included_group_count": len(compacted_groups),
                "omitted_group_count": max(len(groups) - len(compacted_groups), 0),
                "severity_counts": severity_counts,
                "category_counts": category_counts,
                "status_code_counts": status_code_counts,
                "source_key_counts": source_key_counts,
            }
        )
        return compacted

    @staticmethod
    def _compact_group_error_for_prompt(group: dict[str, object]) -> dict[str, object]:
        """Drop bulky grouped-error fields that do not affect LLM decisions."""

        return {
            key: group[key]
            for key in [
                "fingerprint",
                "category",
                "severity",
                "count",
                "source_keys",
                "request_paths",
                "status_codes",
                "levels",
                "message_summary",
                "first_timestamp",
                "last_timestamp",
            ]
            if key in group
        }

    @staticmethod
    def _compact_grep_log_snapshot_result_for_prompt(
        structured_content: dict[str, Any],
        *,
        max_matches: int = 20,
        max_line_chars: int = 160,
    ) -> dict[str, object]:
        """Compact grep output while preserving representative matches."""

        matches: object = structured_content.get("matches", [])
        if not isinstance(matches, list):
            return dict(structured_content)

        compacted_matches: list[object] = [
            MonitoringWorkflowAgent._compact_grep_match_for_prompt(
                match,
                max_line_chars=max_line_chars,
            )
            for match in matches[:max_matches]
        ]
        compacted: dict[str, object] = {
            key: value for key, value in structured_content.items() if key not in {"matches"}
        }
        compacted.update(
            {
                "prompt_compacted": True,
                "matches": compacted_matches,
                "included_match_count": len(compacted_matches),
                "omitted_match_count": max(len(matches) - len(compacted_matches), 0),
            }
        )
        return compacted

    @staticmethod
    def _compact_grep_match_for_prompt(
        match: object,
        *,
        max_line_chars: int,
    ) -> object:
        """Trim grep match text while preserving match metadata and timestamps."""

        if isinstance(match, str):
            return _truncate_prompt_text(match, max_chars=max_line_chars)
        if not isinstance(match, dict):
            return match

        compacted: dict[str, object] = {}
        for key, value in match.items():
            if key in {"line", "message", "text", "raw", "raw_line"} and isinstance(value, str):
                compacted[key] = _truncate_prompt_text(value, max_chars=max_line_chars)
            elif key not in {"context_before", "context_after"}:
                compacted[key] = value
        return compacted

    @staticmethod
    def _build_final_report_not_allowed_message(
        *,
        previous_action: dict[str, object],
        prompt: LogAnalysisPreparedPrompt,
    ) -> Message:
        """Build a correction message when current tool evidence is required first."""

        payload: dict[str, object] = {
            "previous_action": previous_action,
            "final_report_not_allowed_yet": True,
            "next_required_action": prompt.context.next_required_action,
            "final_report_allowed": prompt.context.final_report_allowed,
            "evidence": prompt.context.evidence,
            "current_tool_result_count": prompt.context.current_tool_result_count,
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
                    "evidence": prompt.context.evidence,
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
    ) -> LLMResponse:
        """Ask the configured LLM provider for the next JSON workflow action."""

        request: LLMRequest = LLMRequest(
            messages=tuple(messages),
            options=GenerationOptions(
                temperature=0.0,
                response_format=ResponseFormat.JSON_OBJECT,
            ),
            metadata={
                "workflow_name": workflow.workflow_name,
                "analysis_date": analysis_date.isoformat(),
                "phase": "log_analysis_2b",
                "iteration": str(iteration),
            },
        )
        logger.info(
            "calling LLM for log-analysis workflow action",
            extra={
                "event": "log_analysis_llm_action_start",
                "workflow_name": workflow.workflow_name,
                "iteration": iteration,
                "provider": self.llm_provider.name,
            },
        )
        return self.llm_provider.generate(request)

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
        }
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
                structured_content: dict[str, Any] = await self.mcp_client.call_deterministic_tool(
                    tool_call.tool_name,
                    tool_call.arguments,
                )
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

        zero_line_sources: list[str] = []
        unavailable_sources: list[str] = []
        for project in collect_logs.projects:
            project_name: str = project.project_name
            for source in project.sources:
                source_key: str = source.source_key
                source_name: str = f"{project_name}.{source_key}"
                if source.status == LogSourceCollectionStatus.UNAVAILABLE:
                    unavailable_sources.append(source_name)
                elif source.line_count == 0:
                    zero_line_sources.append(source_name)
        return LogAnalysisCurrentCoverage(
            zero_line_sources=zero_line_sources,
            unavailable_sources=unavailable_sources,
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


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    values: list[int] = []
    for item in value:
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return values


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


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _normalized_group_errors_arguments_for_dedup(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return group_errors arguments normalized for same-scope duplicate detection."""

    normalized: dict[str, Any] = {
        key: value
        for key, value in arguments.items()
        if key not in {"max_groups", "limit", "include_examples"}
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


def _truncate_prompt_text(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[: max_chars - 15]}... [truncated]"
