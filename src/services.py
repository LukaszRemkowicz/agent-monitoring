from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from agents import MonitoringWorkflowAgent
from conf import settings
from db.models import RunStatus
from llm import get_monitoring_llm_provider
from logging_config import get_logger
from mcp import McpWorkflowClient
from repositories import LogAnalysisRepository, SitemapAnalysisRepository
from schemas import (
    LogAnalysisIn,
    LogAnalysisOut,
    LogAnalysisWorkflowResult,
    McpServiceStatus,
    SitemapAnalysisIn,
    SitemapAnalysisOut,
    SitemapAnalysisWorkflowResult,
    WorkflowBootstrap,
)

if TYPE_CHECKING:
    from conf import Settings

logger = get_logger(__name__)
LOG_WORKFLOW_STARTED_SUMMARY = "Workflow preparation started."
LOG_WORKFLOW_READY_SUMMARY = "Workflow bundle loaded; analysis execution is not implemented yet."
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
        force: bool,
        send_email: bool,
    ) -> LogAnalysisWorkflowResult:
        """Run the log-analysis workflow through the monitoring agent."""

        logger.info(
            "preparing log-analysis workflow",
            extra={
                "event": "log_analysis_workflow_prepare_start",
                "analysis_date": str(analysis_date),
                "force": force,
                "send_email": send_email,
            },
        )
        if not force:
            existing: LogAnalysisOut | None = await self.repository.get_by_date(analysis_date)
            if existing is not None:
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
        analysis: LogAnalysisOut = await self.repository.create(analysis_input)
        try:
            workflow: WorkflowBootstrap = await self.agent.run_log_analysis()
        except Exception as exc:
            await self.repository.update(
                analysis,
                status=RunStatus.FAILED,
                finished_at=datetime.now(UTC),
                failure_stage="workflow_bootstrap",
                error_message=str(exc),
            )
            raise
        updated_analysis: LogAnalysisOut = await self.repository.update(
            analysis,
            status=RunStatus.SUCCEEDED,
            finished_at=datetime.now(UTC),
            summary=LOG_WORKFLOW_READY_SUMMARY,
            mcp_artifact=workflow.model_dump(mode="json"),
        )
        logger.info(
            "prepared log-analysis workflow",
            extra={
                "event": "log_analysis_workflow_prepare_done",
                "workflow_name": workflow.workflow_name,
                "tool_count": len(workflow.tools),
            },
        )
        return LogAnalysisWorkflowResult(analysis=updated_analysis, workflow=workflow)

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
            ),
            mcp_client=mcp_client,
            repository=LogAnalysisRepository(),
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
        """Create the Phase 1 sitemap-analysis workflow record."""

        logger.info(
            "preparing sitemap-analysis workflow",
            extra={
                "event": "sitemap_analysis_workflow_prepare_start",
                "analysis_date": str(analysis_date),
                "force": force,
                "send_email": send_email,
            },
        )
        if not force:
            existing: SitemapAnalysisOut | None = await self.repository.get_by_date(analysis_date)
            if existing is not None:
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
        analysis: SitemapAnalysisOut = await self.repository.create(analysis_input)
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
