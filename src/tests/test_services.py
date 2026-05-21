from datetime import UTC, date, datetime

import pytest
from llm_core.protocols import LLMProvider
from llm_core.providers.mock import MockProvider

from agents import MonitoringWorkflowAgent
from conf import Settings
from db.models import RunStatus
from mcp import McpWorkflowClient
from repositories import LogAnalysisRepository, SitemapAnalysisRepository
from schemas import (
    LogAnalysisIn,
    LogAnalysisOut,
    McpServiceStatus,
    SitemapAnalysisIn,
    SitemapAnalysisOut,
    WorkflowBootstrap,
)
from services import LogAnalysisService, SitemapAnalysisService


class FakeWorkflowAgent(MonitoringWorkflowAgent):
    def __init__(self, llm_provider: LLMProvider | None = None) -> None:
        super().__init__(
            FakeMcpClient(),
            llm_provider=llm_provider or MockProvider(),
        )
        self.calls: int = 0

    async def run_log_analysis(self) -> WorkflowBootstrap:
        self.calls += 1
        return WorkflowBootstrap(
            workflow_name="analyze_daily_log_bundle",
            prompt="Prompt",
            mandatory_skills=[],
            optional_skills=[],
            tools=[],
        )


class FailingWorkflowAgent(MonitoringWorkflowAgent):
    def __init__(self) -> None:
        super().__init__(
            FakeMcpClient(),
            llm_provider=MockProvider(),
        )

    async def run_log_analysis(self) -> WorkflowBootstrap:
        raise RuntimeError("MCP unavailable")


class FakeMcpClient(McpWorkflowClient):
    def __init__(self) -> None:
        super().__init__(
            base_url="http://mcp.test/mcp",
            workflow_jwt="test-workflow-jwt",
        )
        self.calls: list[str] = []

    async def get_service_status(self) -> McpServiceStatus:
        self.calls.append("get_service_status")
        return McpServiceStatus(
            name="mcp-log-server",
            status="ok",
            environment="dev",
            client_type="workflow_agent",
        )


class FakeLogAnalysisRepository(LogAnalysisRepository):
    def __init__(self, exists: bool = False) -> None:
        self._has_existing = exists
        self.created: list[dict[str, object]] = []
        self.saved: list[dict[str, object]] = []

    async def get_by_date(self, analysis_date: date) -> LogAnalysisOut | None:
        if self._has_existing:
            return LogAnalysisOut(
                id=1,
                created_at=datetime(2026, 5, 19, tzinfo=UTC),
                analysis_date=analysis_date,
                status=RunStatus.SUCCEEDED,
                summary="Existing analysis.",
            )
        return None

    async def create(self, data: LogAnalysisIn) -> LogAnalysisOut:
        self.created.append(data.model_dump())
        return LogAnalysisOut(
            id=1,
            created_at=datetime(2026, 5, 19, tzinfo=UTC),
            **data.model_dump(),
        )

    async def update(self, analysis: LogAnalysisOut, **updates: object) -> LogAnalysisOut:
        data = analysis.model_dump(exclude={"id", "created_at"})
        data.update(updates)
        updated = LogAnalysisOut(
            id=analysis.id,
            created_at=analysis.created_at,
            **LogAnalysisIn.model_validate(data).model_dump(),
        )
        self.saved.append(updated.model_dump())
        return updated


class FakeSitemapAnalysisRepository(SitemapAnalysisRepository):
    def __init__(self, exists: bool = False) -> None:
        self._has_existing = exists
        self.created: list[dict[str, object]] = []
        self.saved: list[dict[str, object]] = []

    async def get_by_date(self, analysis_date: date) -> SitemapAnalysisOut | None:
        if self._has_existing:
            return SitemapAnalysisOut(
                id=1,
                created_at=datetime(2026, 5, 19, tzinfo=UTC),
                analysis_date=analysis_date,
                status=RunStatus.SUCCEEDED,
                root_sitemap_url="https://example.com/sitemap.xml",
                summary="Existing analysis.",
            )
        return None

    async def create(self, data: SitemapAnalysisIn) -> SitemapAnalysisOut:
        self.created.append(data.model_dump())
        return SitemapAnalysisOut(
            id=1,
            created_at=datetime(2026, 5, 19, tzinfo=UTC),
            **data.model_dump(),
        )

    async def update(self, analysis: SitemapAnalysisOut, **updates: object) -> SitemapAnalysisOut:
        data = analysis.model_dump(exclude={"id", "created_at"})
        data.update(updates)
        updated = SitemapAnalysisOut(
            id=analysis.id,
            created_at=analysis.created_at,
            **SitemapAnalysisIn.model_validate(data).model_dump(),
        )
        self.saved.append(updated.model_dump())
        return updated


class FailingSitemapCompletionRepository(FakeSitemapAnalysisRepository):
    async def update(self, analysis: SitemapAnalysisOut, **updates: object) -> SitemapAnalysisOut:
        if updates.get("status") == RunStatus.SUCCEEDED:
            raise RuntimeError("sitemap preparation failed")
        return await super().update(analysis, **updates)


@pytest.mark.asyncio
async def test_log_analysis_service_loads_workflow_bundle() -> None:
    agent = FakeWorkflowAgent()
    repository = FakeLogAnalysisRepository()
    service = LogAnalysisService(
        agent=agent,
        mcp_client=FakeMcpClient(),
        repository=repository,
    )

    result = await service.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        force=False,
        send_email=True,
    )

    assert result.workflow.workflow_name == "analyze_daily_log_bundle"
    assert agent.calls == 1
    assert repository.created[0]["analysis_date"] == date(2026, 5, 19)
    assert repository.created[0]["status"] == RunStatus.RUNNING.value
    assert repository.created[0]["summary"] == "Workflow preparation started."
    assert result.analysis.status == RunStatus.SUCCEEDED.value
    assert result.analysis.mcp_artifact == result.workflow.model_dump(mode="json")
    assert repository.saved[0]["status"] == RunStatus.SUCCEEDED.value
    assert repository.saved[0]["summary"] == (
        "Workflow bundle loaded; analysis execution is not implemented yet."
    )


@pytest.mark.asyncio
async def test_log_analysis_service_records_failure_state() -> None:
    repository = FakeLogAnalysisRepository()
    service = LogAnalysisService(
        agent=FailingWorkflowAgent(),
        mcp_client=FakeMcpClient(),
        repository=repository,
    )

    with pytest.raises(RuntimeError, match="MCP unavailable"):
        await service.run_log_analysis(
            analysis_date=date(2026, 5, 19),
            force=False,
            send_email=True,
        )

    assert repository.saved[0]["status"] == RunStatus.FAILED.value
    assert repository.saved[0]["failure_stage"] == "workflow_bootstrap"
    assert repository.saved[0]["error_message"] == "MCP unavailable"


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

    assert isinstance(service.agent.llm_provider, MockProvider)


@pytest.mark.asyncio
async def test_sitemap_analysis_service_creates_workflow_record() -> None:
    repository = FakeSitemapAnalysisRepository()
    service = SitemapAnalysisService(
        repository=repository,
        root_sitemap_url="https://example.com/sitemap.xml",
    )

    result = await service.run_sitemap_analysis(
        analysis_date=date(2026, 5, 19),
        force=False,
        send_email=True,
    )

    assert result.analysis.analysis_date == repository.created[0]["analysis_date"]
    assert repository.created[0]["analysis_date"] == date(2026, 5, 19)
    assert repository.created[0]["root_sitemap_url"] == "https://example.com/sitemap.xml"
    assert repository.created[0]["status"] == RunStatus.RUNNING.value
    assert repository.created[0]["summary"] == "Sitemap workflow preparation started."
    assert result.analysis.status == RunStatus.SUCCEEDED.value
    assert repository.saved[0]["status"] == RunStatus.SUCCEEDED.value
    assert repository.saved[0]["summary"] == "Sitemap analysis workflow record prepared."


@pytest.mark.asyncio
async def test_sitemap_analysis_service_records_failure_state() -> None:
    repository = FailingSitemapCompletionRepository()
    service = SitemapAnalysisService(
        repository=repository,
        root_sitemap_url="https://example.com/sitemap.xml",
    )

    with pytest.raises(RuntimeError, match="sitemap preparation failed"):
        await service.run_sitemap_analysis(
            analysis_date=date(2026, 5, 19),
            force=False,
            send_email=True,
        )

    assert repository.created[0]["status"] == RunStatus.RUNNING.value
    assert repository.saved[0]["status"] == RunStatus.FAILED.value
    assert repository.saved[0]["failure_stage"] == "workflow_preparation"
    assert repository.saved[0]["error_message"] == "sitemap preparation failed"


@pytest.mark.asyncio
async def test_sitemap_analysis_service_blocks_existing_date_without_force() -> None:
    repository = FakeSitemapAnalysisRepository(exists=True)
    service = SitemapAnalysisService(
        repository=repository,
        root_sitemap_url="https://example.com/sitemap.xml",
    )

    with pytest.raises(ValueError, match="already exists"):
        await service.run_sitemap_analysis(
            analysis_date=date(2026, 5, 19),
            force=False,
            send_email=True,
        )

    assert repository.created == []
