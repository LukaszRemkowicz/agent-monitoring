from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from agents import MonitoringWorkflowAgent
from conf import settings
from llm import get_monitoring_llm_provider
from logging_config import get_logger
from mcp import McpWorkflowClient
from protocols import LogAnalysisAgent, LogAnalysisReader, McpStatusClient
from repositories import LogAnalysisRepository
from schemas import LogAnalysisWorkflowResult, McpServiceStatus, WorkflowBootstrap

if TYPE_CHECKING:
    from conf import Settings

logger = get_logger(__name__)


class LogAnalysisService:
    """Business service for the log-analysis command flow."""

    def __init__(
        self,
        *,
        agent: LogAnalysisAgent,
        mcp_client: McpStatusClient,
        repository: LogAnalysisReader,
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
        analysis_date: date | None,
        force: bool,
        send_email: bool,
    ) -> LogAnalysisWorkflowResult:
        """Run the log-analysis workflow through the monitoring agent."""

        logger.info(
            "preparing log-analysis workflow",
            extra={
                "event": "log_analysis_workflow_prepare_start",
                "analysis_date": str(analysis_date) if analysis_date else None,
                "force": force,
                "send_email": send_email,
            },
        )
        if analysis_date is not None and not force:
            existing: object | None = await self.repository.get_by_date(analysis_date)
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

        workflow: WorkflowBootstrap = await self.agent.run_log_analysis()
        logger.info(
            "prepared log-analysis workflow",
            extra={
                "event": "log_analysis_workflow_prepare_done",
                "workflow_name": workflow.workflow_name,
                "tool_count": len(workflow.tools),
            },
        )
        return LogAnalysisWorkflowResult(workflow=workflow)

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
