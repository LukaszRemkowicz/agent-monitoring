from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import TYPE_CHECKING

from agents import MonitoringWorkflowAgent
from conf import settings
from db.models import RunStatus
from llm import get_monitoring_llm_provider
from logging_config import get_logger
from mcp import McpWorkflowClient
from monitoring_context import load_private_monitoring_context
from repositories import LogAnalysisRepository, SitemapAnalysisRepository
from schemas import (
    LogAnalysisAgentContext,
    LogAnalysisIn,
    LogAnalysisOut,
    LogAnalysisWorkflowResult,
    LogCollectionWindow,
    McpServiceStatus,
    SitemapAnalysisIn,
    SitemapAnalysisOut,
    SitemapAnalysisWorkflowResult,
)

if TYPE_CHECKING:
    from conf import Settings

logger = get_logger(__name__)
LOG_WORKFLOW_STARTED_SUMMARY = "Workflow preparation started."
SITEMAP_WORKFLOW_STARTED_SUMMARY = "Sitemap workflow preparation started."
SITEMAP_WORKFLOW_READY_SUMMARY = "Sitemap analysis workflow record prepared."


class LogAnalysisService:
    """Business service for the log-analysis command flow."""

    def __init__(
        self,
        *,
        agent: MonitoringWorkflowAgent,
        mcp_client: McpWorkflowClient,
        repository: LogAnalysisRepository,
    ) -> None:
        self.agent = agent
        self.mcp_client = mcp_client
        self.repository = repository

    async def check_mcp_status(self) -> McpServiceStatus:
        """Return MCP service status for command-line diagnostics."""

        logger.info(
            "checking MCP service status",
            extra={"event": "mcp_status_check_start"},
        )
        status: McpServiceStatus = await self.mcp_client.get_service_status()
        logger.info(
            "checked MCP service status",
            extra={
                "event": "mcp_status_check_done",
                "status": status.status,
                "environment": status.environment,
                "client_type": status.client_type,
            },
        )
        return status

    async def run_log_analysis(
        self,
        *,
        analysis_date: date,
        log_window: LogCollectionWindow,
        force: bool,
        send_email: bool,
    ) -> LogAnalysisWorkflowResult:
        """Run the log-analysis workflow through the monitoring agent."""

        execution_started_at: float = monotonic()
        logger.info(
            "preparing log-analysis workflow",
            extra={
                "event": "log_analysis_workflow_prepare_start",
                "analysis_date": str(analysis_date),
                "force": force,
                "send_email": send_email,
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
            agent_context: LogAnalysisAgentContext = await self.agent.run_log_analysis(
                analysis_date=analysis_date,
                log_window=log_window,
            )
        except Exception as exc:
            execution_time_seconds: float = round(monotonic() - execution_started_at, 3)
            await self.repository.update(
                analysis,
                status=RunStatus.FAILED,
                finished_at=datetime.now(UTC),
                failure_stage="log_analysis",
                error_message=str(exc),
                execution_time_seconds=execution_time_seconds,
            )
            raise
        execution_time_seconds = round(monotonic() - execution_started_at, 3)
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
            log_window_since=log_window.since_datetime,
            log_window_until=log_window.until_datetime,
            gpt_tokens_used=agent_context.llm_tokens_used,
            gpt_cost_usd=agent_context.llm_cost_usd,
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

    @classmethod
    def create_default(cls, _settings: Settings = settings) -> LogAnalysisService:
        mcp_client = McpWorkflowClient(
            base_url=_settings.LOG_ANALYSIS_MCP_URL,
            workflow_jwt=_settings.MCP_WORKFLOW_JWT,
        )
        return cls(
            agent=MonitoringWorkflowAgent(
                mcp_client,
                llm_provider=get_monitoring_llm_provider(_settings),
                private_monitoring_context=load_private_monitoring_context(
                    _settings.MONITORING_PRIVATE_CONTEXT_PATH
                ),
            ),
            mcp_client=mcp_client,
            repository=LogAnalysisRepository(),
        )

    @staticmethod
    def create_log_collection_window(analysis_date: date) -> LogCollectionWindow:
        log_window_since: datetime = datetime.combine(analysis_date, time.min, tzinfo=UTC)
        log_window_until: datetime = log_window_since + timedelta(days=1)
        return LogCollectionWindow(
            since=_format_mcp_timestamp(log_window_since),
            until=_format_mcp_timestamp(log_window_until),
            since_datetime=log_window_since,
            until_datetime=log_window_until,
        )


class SitemapAnalysisService:
    """Business service for the sitemap-analysis command flow."""

    def __init__(
        self,
        *,
        repository: SitemapAnalysisRepository,
        root_sitemap_url: str,
    ) -> None:
        self.repository = repository
        self.root_sitemap_url = root_sitemap_url

    async def run_sitemap_analysis(
        self,
        *,
        analysis_date: date,
        force: bool,
        send_email: bool,
    ) -> SitemapAnalysisWorkflowResult:
        """Create the sitemap-analysis workflow record."""

        logger.info(
            "preparing sitemap-analysis workflow",
            extra={
                "event": "sitemap_analysis_workflow_prepare_start",
                "analysis_date": str(analysis_date),
                "force": force,
                "send_email": send_email,
            },
        )
        existing: SitemapAnalysisOut | None = await self.repository.get_by_date(analysis_date)
        if existing is not None and not force:
            logger.info(
                "sitemap analysis already exists for analysis date",
                extra={
                    "event": "sitemap_analysis_workflow_prepare_skipped",
                    "analysis_date": str(analysis_date),
                    "reason": "existing_analysis",
                },
            )
            msg = (
                f"Sitemap analysis already exists for {analysis_date}. "
                "Use --force to prepare a new workflow record."
            )
            raise ValueError(msg)

        analysis_input: SitemapAnalysisIn = SitemapAnalysisIn(
            analysis_date=analysis_date,
            status=RunStatus.RUNNING,
            started_at=datetime.now(UTC),
            root_sitemap_url=self.root_sitemap_url,
            summary=SITEMAP_WORKFLOW_STARTED_SUMMARY,
        )
        if existing is not None:
            analysis: SitemapAnalysisOut = await self.repository.update(
                existing,
                **analysis_input.model_dump(exclude={"analysis_date"}),
            )
        else:
            analysis = await self.repository.create(analysis_input)
        try:
            updated_analysis: SitemapAnalysisOut = await self.repository.update(
                analysis,
                status=RunStatus.SUCCEEDED,
                finished_at=datetime.now(UTC),
                summary=SITEMAP_WORKFLOW_READY_SUMMARY,
            )
        except Exception as exc:
            await self.repository.update(
                analysis,
                status=RunStatus.FAILED,
                finished_at=datetime.now(UTC),
                failure_stage="workflow_preparation",
                error_message=str(exc),
            )
            raise
        logger.info(
            "prepared sitemap-analysis workflow",
            extra={
                "event": "sitemap_analysis_workflow_prepare_done",
                "analysis_date": str(analysis_date),
            },
        )
        return SitemapAnalysisWorkflowResult(analysis=updated_analysis)

    @classmethod
    def create_default(cls, _settings: Settings = settings) -> SitemapAnalysisService:
        return cls(
            repository=SitemapAnalysisRepository(),
            root_sitemap_url=_settings.SITEMAP_ROOT_URL,
        )


def _format_mcp_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
