from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from conf import settings
from db.models import RunStatus
from exceptions import LogAnalysisAgentError, format_exception_chain
from logging_config import get_logger
from repositories import LLMCallRepository, LogAnalysisRepository
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogAnalysisFingerprintPacket,
    LogAnalysisIn,
    LogAnalysisOut,
    LogAnalysisWorkflowResult,
    LogCollectionWindow,
)
from services.log_fingerprints import LogAnalysisFingerprintBuilder

if TYPE_CHECKING:
    from agents import MonitoringWorkflowAgent

logger = get_logger(__name__)
LOG_WORKFLOW_STARTED_SUMMARY = "Workflow preparation started."


class LogAnalysisService:
    """Business service for the log-analysis command flow."""

    def __init__(
        self,
        *,
        agent: MonitoringWorkflowAgent,
        repository: LogAnalysisRepository,
        llm_call_repository: LLMCallRepository | None = None,
    ) -> None:
        self.agent = agent
        self.repository = repository
        self.agent.llm_call_repository = llm_call_repository

    async def run_log_analysis(
        self,
        *,
        analysis_date: date,
        log_window: LogCollectionWindow,
        force: bool,
    ) -> LogAnalysisWorkflowResult:
        """Run the log-analysis workflow through the monitoring agent."""

        execution_started_at: float = monotonic()
        logger.info(
            "preparing log-analysis workflow",
            extra={
                "event": "log_analysis_workflow_prepare_start",
                "analysis_date": str(analysis_date),
                "force": force,
            },
        )
        existing: LogAnalysisOut | None = await self.repository.get_by_date(analysis_date)
        if existing is not None and not force:
            logger.info(
                "log analysis already exists for analysis date",
                extra={
                    "event": "log_analysis_workflow_prepare_skipped",
                    "analysis_date": str(analysis_date),
                    "reason": "existing_analysis",
                },
            )
            msg = (
                f"Log analysis already exists for {analysis_date}. "
                "Use --force to load a new workflow bundle."
            )
            raise ValueError(msg)

        analysis_input: LogAnalysisIn = LogAnalysisIn(
            analysis_date=analysis_date,
            status=RunStatus.RUNNING,
            started_at=datetime.now(UTC),
            summary=LOG_WORKFLOW_STARTED_SUMMARY,
        )
        if existing is not None:
            analysis: LogAnalysisOut = await self.repository.update(
                existing,
                **analysis_input.model_dump(exclude={"analysis_date"}),
            )
        else:
            analysis = await self.repository.create(analysis_input)
        try:
            historical_context: str = await self._build_historical_context(analysis_date)
            previous_analysis: LogAnalysisOut | None = await self.repository.get_latest_before_date(
                analysis_date
            )
            logger.info(
                "loaded previous log-analysis for monitoring agent",
                extra={
                    "event": "log_analysis_previous_analysis_loaded",
                    "analysis_date": str(analysis_date),
                    "previous_analysis_found": previous_analysis is not None,
                    "previous_analysis_date": (
                        str(previous_analysis.analysis_date)
                        if previous_analysis is not None
                        else None
                    ),
                    "previous_analysis_severity": (
                        previous_analysis.severity if previous_analysis is not None else None
                    ),
                },
            )
            agent_context: LogAnalysisAgentContext = await self.agent.run_log_analysis(
                analysis_date=analysis_date,
                log_window=log_window,
                historical_context=historical_context,
                previous_analysis=previous_analysis,
            )
        except Exception as exc:
            execution_time_seconds: float = round(monotonic() - execution_started_at, 3)
            error_detail: str = format_exception_chain(exc)
            partial_collect_logs: CollectLogsArtifact | None = (
                exc.collect_logs if isinstance(exc, LogAnalysisAgentError) else None
            )
            failure_artifact: dict[str, object] = self._build_failure_artifact(
                analysis_date=analysis_date,
                log_window=log_window,
                exc=exc,
                partial_collect_logs=partial_collect_logs,
            )
            failure_coverage_snapshot: dict[str, object] = (
                LogAnalysisFingerprintBuilder.build_coverage_snapshot(partial_collect_logs)
                if partial_collect_logs is not None
                else {}
            )
            logger.error(
                "log-analysis workflow failed",
                extra={
                    "event": "log_analysis_workflow_failed",
                    "analysis_date": str(analysis_date),
                    "failure_stage": "log_analysis",
                    "execution_time_seconds": execution_time_seconds,
                    "error": error_detail,
                },
            )
            await self.repository.update(
                analysis,
                status=RunStatus.FAILED,
                finished_at=datetime.now(UTC),
                failure_stage="log_analysis",
                severity="CRITICAL",
                summary="Log-analysis workflow failed before a final report was produced.",
                key_findings=[
                    f"log_analysis failed with {type(exc).__name__}: {error_detail}",
                ],
                recommendations=(
                    "Inspect command logs, MCP availability, and persisted failure details; "
                    "rerun with --force after the underlying issue is fixed."
                ),
                trend_summary="No trend summary is available because the workflow failed.",
                mcp_artifact=failure_artifact,
                mcp_collect_logs_id=(
                    self._resolve_collect_logs_reference(partial_collect_logs)
                    if partial_collect_logs is not None
                    else None
                ),
                coverage_snapshot=failure_coverage_snapshot,
                log_window_since=log_window.since_datetime,
                log_window_until=log_window.until_datetime,
                error_message=error_detail,
                execution_time_seconds=execution_time_seconds,
            )
            raise
        execution_time_seconds = round(monotonic() - execution_started_at, 3)
        fingerprint_packet: LogAnalysisFingerprintPacket = LogAnalysisFingerprintBuilder.build(
            collect_logs=agent_context.collect_logs,
            tool_results=agent_context.tool_results,
            final_report=agent_context.final_report,
            log_window_since=agent_context.log_window_since,
            log_window_until=agent_context.log_window_until,
        )
        updated_analysis: LogAnalysisOut = await self.repository.update(
            analysis,
            status=RunStatus.SUCCEEDED,
            finished_at=datetime.now(UTC),
            summary=agent_context.final_report.summary,
            severity=agent_context.final_report.severity,
            key_findings=agent_context.final_report.key_findings,
            recommendations=agent_context.final_report.recommendations,
            trend_summary=agent_context.final_report.trend_summary,
            mcp_artifact=agent_context.model_dump(mode="json"),
            mcp_collect_logs_id=self._resolve_collect_logs_reference(agent_context.collect_logs),
            log_window_since=log_window.since_datetime,
            log_window_until=log_window.until_datetime,
            gpt_tokens_used=agent_context.llm_tokens_used,
            gpt_cost_usd=agent_context.llm_cost_usd,
            fingerprints=fingerprint_packet.fingerprints,
            evidence_fingerprints=fingerprint_packet.evidence_fingerprints,
            known_patterns=fingerprint_packet.known_patterns,
            coverage_snapshot=fingerprint_packet.coverage_snapshot,
            fingerprint_version=fingerprint_packet.fingerprint_version,
            execution_time_seconds=execution_time_seconds,
        )
        logger.info(
            "prepared log-analysis workflow",
            extra={
                "event": "log_analysis_workflow_prepare_done",
                "workflow_name": agent_context.workflow.workflow_name,
                "tool_count": len(agent_context.workflow.tools),
                "execution_time_seconds": execution_time_seconds,
            },
        )
        return LogAnalysisWorkflowResult(analysis=updated_analysis, agent_context=agent_context)

    @staticmethod
    def _build_failure_artifact(
        *,
        analysis_date: date,
        log_window: LogCollectionWindow,
        exc: Exception,
        partial_collect_logs: CollectLogsArtifact | None,
    ) -> dict[str, object]:
        artifact: dict[str, object] = {
            "analysis_date": str(analysis_date),
            "log_window": {
                "since": log_window.since,
                "until": log_window.until,
            },
            "error": {
                "stage": "log_analysis",
                "type": type(exc).__name__,
                "message": str(exc),
                "detail": format_exception_chain(exc),
            },
        }
        if partial_collect_logs is not None:
            artifact["collect_logs"] = partial_collect_logs.model_dump(mode="json")
        return artifact

    @staticmethod
    def _resolve_collect_logs_reference(collect_logs: CollectLogsArtifact) -> str | None:
        if collect_logs.session_id:
            return collect_logs.session_id
        for project in collect_logs.projects:
            if project.snapshot_dir:
                return project.snapshot_dir
        return None

    @staticmethod
    def create_log_collection_window(analysis_date: date) -> LogCollectionWindow:
        local_timezone = ZoneInfo(settings.LOG_TIMEZONE)
        local_window_since = datetime.combine(analysis_date, time.min, tzinfo=local_timezone)
        local_window_until = local_window_since + timedelta(days=1)
        log_window_since: datetime = local_window_since.astimezone(UTC)
        log_window_until: datetime = local_window_until.astimezone(UTC)
        return LogCollectionWindow(
            since=_format_mcp_timestamp(log_window_since),
            until=_format_mcp_timestamp(log_window_until),
            since_datetime=log_window_since,
            until_datetime=log_window_until,
        )

    async def _build_historical_context(self, analysis_date: date) -> str:
        """Return landingpage-style markdown context from recent stored reports."""

        historical_runs: list[LogAnalysisOut] = await self.repository.last_5_days(analysis_date)
        historical_context: str = HistoricalContextBuilder.build(historical_runs)
        if historical_context:
            logger.info(
                "historical context loaded for monitoring agent",
                extra={
                    "event": "log_analysis_historical_context_loaded",
                    "analysis_date": str(analysis_date),
                    "historical_run_count": len(historical_runs),
                    "historical_context_chars": len(historical_context),
                },
            )
        else:
            logger.info(
                "no historical context available for monitoring agent",
                extra={
                    "event": "log_analysis_historical_context_empty",
                    "analysis_date": str(analysis_date),
                },
            )
        return historical_context


class HistoricalContextBuilder:
    """Format stored log-analysis reports into the landingpage historical block."""

    @staticmethod
    def build(records: list[LogAnalysisOut]) -> str:
        """Return markdown context for recent analyses, or an empty string."""

        if not records:
            return ""

        lines: list[str] = []
        for record in records:
            lines.append(
                f"## {record.analysis_date} — Severity: {record.severity}\n"
                f"Summary: {record.summary}\n"
                f"Key findings: {record.key_findings}\n"
                f"Recommendations: {record.recommendations}"
            )
        return "\n\n".join(lines)


def _format_mcp_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
