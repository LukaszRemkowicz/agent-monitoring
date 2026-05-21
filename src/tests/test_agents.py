import pytest
from llm_core.providers.mock import MockProvider

from agents import MonitoringWorkflowAgent
from mcp import McpWorkflowClient
from schemas import WorkflowBootstrap


class FakeMcpWorkflowClient(McpWorkflowClient):
    def __init__(self) -> None:
        super().__init__(
            base_url="http://mcp.test/mcp",
            workflow_jwt="test-workflow-jwt",
        )
        self.calls: list[str] = []

    async def get_workflow_bundle(self) -> WorkflowBootstrap:
        self.calls.append("get_workflow_bundle")
        return WorkflowBootstrap(
            workflow_name="analyze_daily_log_bundle",
            prompt="Log Summary Instructions",
            mandatory_skills=[],
            optional_skills=[],
            tools=[],
        )


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_starts_by_loading_mcp_bootstrap() -> None:
    mcp_client = FakeMcpWorkflowClient()
    agent = MonitoringWorkflowAgent(mcp_client, llm_provider=MockProvider())

    workflow = await agent.run_log_analysis()

    assert mcp_client.calls == ["get_workflow_bundle"]
    assert workflow.workflow_name == "analyze_daily_log_bundle"
