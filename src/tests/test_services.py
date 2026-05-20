from datetime import date
from typing import cast

import pytest
from llm_core.providers.mock import MockProvider

from agents import MonitoringWorkflowAgent
from conf import Settings
from schemas import McpServiceStatus, WorkflowBootstrap
from services import LogAnalysisService


class FakeWorkflowAgent:
    def __init__(self, llm_provider: object | None = None) -> None:
        self.calls: int = 0
        self.llm_provider = llm_provider

    async def run_log_analysis(self) -> WorkflowBootstrap:
        self.calls += 1
        return WorkflowBootstrap(
            workflow_name="analyze_daily_log_bundle",
            prompt="Prompt",
            mandatory_skills=[],
            optional_skills=[],
            tools=[],
        )


class FakeMcpClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_service_status(self) -> McpServiceStatus:
        self.calls.append("get_service_status")
        return McpServiceStatus(
            name="mcp-log-server",
            status="ok",
            environment="dev",
            client_type="workflow_agent",
        )


class FakeLogAnalysisRepository:
    def __init__(self, exists: bool = False) -> None:
        self.exists = exists

    async def get_by_date(self, analysis_date: date) -> object | None:
        if self.exists:
            return object()
        return None


@pytest.mark.asyncio
async def test_log_analysis_service_loads_workflow_bundle() -> None:
    agent = FakeWorkflowAgent()
    service = LogAnalysisService(
        agent=agent,
        mcp_client=FakeMcpClient(),
        repository=FakeLogAnalysisRepository(),
    )

    result = await service.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        force=False,
        send_email=True,
    )

    assert result.workflow.workflow_name == "analyze_daily_log_bundle"
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_log_analysis_service_blocks_existing_date_without_force() -> None:
    agent = FakeWorkflowAgent()
    service = LogAnalysisService(
        agent=agent,
        mcp_client=FakeMcpClient(),
        repository=FakeLogAnalysisRepository(exists=True),
    )

    with pytest.raises(ValueError, match="already exists"):
        await service.run_log_analysis(
            analysis_date=date(2026, 5, 19),
            force=False,
            send_email=True,
        )

    assert agent.calls == 0


@pytest.mark.asyncio
async def test_log_analysis_service_allows_existing_date_with_force() -> None:
    agent = FakeWorkflowAgent()
    service = LogAnalysisService(
        agent=agent,
        mcp_client=FakeMcpClient(),
        repository=FakeLogAnalysisRepository(exists=True),
    )

    result = await service.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        force=True,
        send_email=True,
    )

    assert result.workflow.workflow_name == "analyze_daily_log_bundle"
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_log_analysis_service_checks_mcp_status() -> None:
    mcp_client = FakeMcpClient()
    service = LogAnalysisService(
        agent=FakeWorkflowAgent(),
        mcp_client=mcp_client,
        repository=FakeLogAnalysisRepository(),
    )

    status: McpServiceStatus = await service.check_mcp_status()

    assert mcp_client.calls == ["get_service_status"]
    assert status.name == "mcp-log-server"
    assert status.status == "ok"


def test_log_analysis_service_default_agent_uses_configured_llm_provider() -> None:
    service = LogAnalysisService.create_default(
        Settings(
            {
                "LOG_ANALYSIS_MCP_URL": "http://mcp.local/mcp",
                "MCP_WORKFLOW_JWT": "jwt-token",
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "",
                "MONITORING_LLM_PROVIDER": "mock",
                "MONITORING_LLM_FAST_MODEL": "gpt-4.1-mini",
                "MONITORING_LLM_STRONG_MODEL": "gpt-5",
            }
        )
    )

    default_agent = cast(MonitoringWorkflowAgent, service.agent)
    assert isinstance(default_agent.llm_provider, MockProvider)
