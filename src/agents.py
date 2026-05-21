from __future__ import annotations

from llm_core.protocols import LLMProvider

from logging_config import get_logger
from mcp import McpWorkflowClient
from schemas import WorkflowBootstrap

logger = get_logger(__name__)


class MonitoringWorkflowAgent:
    """Agent boundary for MCP-backed monitoring workflow bootstrap calls."""

    def __init__(self, mcp_client: McpWorkflowClient, llm_provider: LLMProvider) -> None:
        self.mcp_client = mcp_client
        self.llm_provider = llm_provider

    async def run_log_analysis(self) -> WorkflowBootstrap:
        """Run the first log-analysis agent step by loading the MCP bootstrap bundle."""

        logger.info(
            "loading MCP daily log workflow bundle",
            extra={"event": "workflow_bundle_load_start"},
        )
        workflow: WorkflowBootstrap = await self.mcp_client.get_workflow_bundle()
        logger.info(
            "loaded MCP daily log workflow bundle",
            extra={
                "event": "workflow_bundle_load_done",
                "workflow_name": workflow.workflow_name,
                "mandatory_skill_count": len(workflow.mandatory_skills),
                "optional_skill_count": len(workflow.optional_skills),
                "tool_count": len(workflow.tools),
            },
        )
        return workflow
