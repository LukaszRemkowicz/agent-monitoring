from datetime import UTC, date, datetime

import pytest
from llm_core.protocols import LLMProvider
from llm_core.providers.mock import MockProvider
from pytest_mock import MockerFixture

from agents import MonitoringWorkflowAgent
from conf import Settings
from db.models import RunStatus
from mcp import McpWorkflowClient
from repositories import LogAnalysisRepository, SitemapAnalysisRepository
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogAnalysisFinalReport,
    LogAnalysisIn,
    LogAnalysisOut,
    LogAnalysisPreparedPrompt,
    LogAnalysisPromptContext,
    LogCollectionWindow,
    McpServiceStatus,
    ProjectManifestSummary,
    SitemapAnalysisIn,
    SitemapAnalysisOut,
    SnapshotAccessGuidance,
    WorkflowBootstrap,
)
from services import LogAnalysisService, SitemapAnalysisService
from tests.conftest import build_collect_logs_artifact_payload

PRIVATE_MONITORING_CONTEXT = "# Private VPS Monitoring Context\n\nTest context."


class FakeWorkflowAgent(MonitoringWorkflowAgent):
    def __init__(self, llm_provider: LLMProvider | None = None) -> None:
        super().__init__(
            FakeMcpClient(),
            llm_provider=llm_provider or MockProvider(),
            private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
        )
        self.calls: int = 0

    async def run_log_analysis(
        self,
        *,
        analysis_date: date,
        log_window: LogCollectionWindow,
    ) -> LogAnalysisAgentContext:
        self.calls += 1
        workflow = WorkflowBootstrap(
            workflow_name="analyze_daily_log_bundle",
            prompt="Prompt",
            mandatory_skills=[],
            optional_skills=[],
            tools=[],
        )
        collect_logs = CollectLogsArtifact.model_validate(
            build_collect_logs_artifact_payload(
                requested_project_names=["landingpage", "shop"],
                next_step_tips=[],
                resolved_source_keys=["backend"],
            )
        )
        prompt = LogAnalysisPreparedPrompt(
            system_prompt="Prompt",
            context=LogAnalysisPromptContext(
                analysis_date=analysis_date,
                workflow_name=workflow.workflow_name,
                current_phase="final_report",
                completed_steps=[
                    "analyze_daily_log_bundle",
                    "read_mandatory_skills",
                    "list_projects",
                    "collect_logs",
                ],
                allowed_actions=["call_tools", "read_skills", "final_report"],
                next_required_action="call_tools",
                final_report_allowed=False,
                available_projects=[
                    ProjectManifestSummary(
                        project_name="landingpage",
                        project_summary="Landingpage project.",
                        source_keys=["backend"],
                    ),
                    ProjectManifestSummary(
                        project_name="shop",
                        project_summary="Shop project.",
                        source_keys=["backend"],
                    ),
                ],
                mandatory_skills=[],
                optional_skills=[],
                collection=collect_logs,
                snapshot_access=SnapshotAccessGuidance(
                    workspace="workflow",
                    session_id=None,
                    session_id_is_for_session_workspace_only=True,
                    workflow_followup_arguments=["project_name", "archive_name"],
                    instruction="Use project_name for workflow follow-up tools.",
                ),
                available_tools=[],
                report_contract={
                    "summary": "string",
                    "severity": "INFO|WARNING|CRITICAL",
                    "severity_rationale": "string",
                    "key_findings": "list[string]",
                    "evidence": "list[string]",
                    "coverage_gaps": "list[string]",
                    "recommendations": "string",
                    "watch_only_items": "list[string]",
                    "trend_summary": "string",
                },
                instructions=[
                    "Use deterministic MCP snapshot tools before final report.",
                ],
            ),
        )
        return LogAnalysisAgentContext(
            workflow=workflow,
            collect_logs=collect_logs,
            prompt=prompt,
            final_report=LogAnalysisFinalReport(
                action="final_report",
                summary="Landingpage logs are healthy.",
                severity="INFO",
                severity_rationale="INFO because no service-impacting issue was found.",
                key_findings=["No critical incidents found."],
                evidence=["group_errors found no repeated errors."],
                coverage_gaps=[],
                recommendations="Keep watching the backend logs.",
                watch_only_items=["Routine bot traffic."],
                trend_summary="No prior trend data was available.",
            ),
            log_window_since=datetime(2026, 5, 19, tzinfo=UTC),
            log_window_until=datetime(2026, 5, 20, tzinfo=UTC),
            llm_tokens_used=123,
            llm_cost_usd=0.02,
            llm_report_execution_time_seconds=4.32,
        )


class FailingWorkflowAgent(MonitoringWorkflowAgent):
    def __init__(self) -> None:
        super().__init__(
            FakeMcpClient(),
            llm_provider=MockProvider(),
            private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
        )

    async def run_log_analysis(
        self,
        *,
        analysis_date: date,
        log_window: LogCollectionWindow,
    ) -> LogAnalysisAgentContext:
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
        log_window=LogAnalysisService.create_log_collection_window(date(2026, 5, 19)),
        force=False,
        send_email=True,
    )

    assert result.workflow.workflow_name == "analyze_daily_log_bundle"
    assert agent.calls == 1
    assert repository.created[0]["analysis_date"] == date(2026, 5, 19)
    assert repository.created[0]["status"] == RunStatus.RUNNING.value
    assert repository.created[0]["summary"] == "Workflow preparation started."
    assert result.analysis.status == RunStatus.SUCCEEDED.value
    assert result.analysis.mcp_artifact == result.agent_context.model_dump(mode="json")
    assert result.analysis.log_window_since == datetime(2026, 5, 19, tzinfo=UTC)
    assert result.analysis.log_window_until == datetime(2026, 5, 20, tzinfo=UTC)
    assert '"analysis_date": "2026-05-19"' in result.prepared_prompt.user_prompt
    assert result.prepared_prompt.context.collection.projects[0].snapshot_dir == (
        "workflow/landingpage/latest"
    )
    assert repository.saved[0]["status"] == RunStatus.SUCCEEDED.value
    assert repository.saved[0]["summary"] == "Landingpage logs are healthy."
    assert repository.saved[0]["severity"] == "INFO"
    assert repository.saved[0]["key_findings"] == ["No critical incidents found."]
    assert repository.saved[0]["recommendations"] == "Keep watching the backend logs."
    assert repository.saved[0]["trend_summary"] == "No prior trend data was available."
    assert repository.saved[0]["gpt_tokens_used"] == 123
    assert repository.saved[0]["gpt_cost_usd"] == 0.02
    assert result.agent_context.llm_report_execution_time_seconds == 4.32


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
            log_window=LogAnalysisService.create_log_collection_window(date(2026, 5, 19)),
            force=False,
            send_email=True,
        )

    assert repository.saved[0]["status"] == RunStatus.FAILED.value
    assert repository.saved[0]["failure_stage"] == "log_analysis"
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
            log_window=LogAnalysisService.create_log_collection_window(date(2026, 5, 19)),
            force=False,
            send_email=True,
        )

    assert agent.calls == 0


@pytest.mark.asyncio
async def test_log_analysis_service_allows_existing_date_with_force() -> None:
    agent = FakeWorkflowAgent()
    repository = FakeLogAnalysisRepository(exists=True)
    service = LogAnalysisService(
        agent=agent,
        mcp_client=FakeMcpClient(),
        repository=repository,
    )

    result = await service.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogAnalysisService.create_log_collection_window(date(2026, 5, 19)),
        force=True,
        send_email=True,
    )

    assert result.workflow.workflow_name == "analyze_daily_log_bundle"
    assert agent.calls == 1
    assert repository.created == []
    assert repository.saved[0]["status"] == RunStatus.RUNNING.value
    assert repository.saved[-1]["status"] == RunStatus.SUCCEEDED.value


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
                "MONITORING_PROJECT": "landingpage",
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "",
                "MONITORING_LLM_PROVIDER": "mock",
                "MONITORING_LLM_FAST_MODEL": "gpt-4.1-mini",
                "MONITORING_LLM_STRONG_MODEL": "gpt-5",
                "MONITORING_PRIVATE_CONTEXT_PATH": __file__,
            }
        )
    )

    assert isinstance(service.agent.llm_provider, MockProvider)


@pytest.mark.asyncio
async def test_log_analysis_service_records_execution_time(
    mocker: MockerFixture,
) -> None:
    agent = FakeWorkflowAgent()
    repository = FakeLogAnalysisRepository()
    service = LogAnalysisService(
        agent=agent,
        mcp_client=FakeMcpClient(),
        repository=repository,
    )
    monotonic = mocker.patch("services.monotonic", side_effect=[100.0, 103.25])

    result = await service.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogAnalysisService.create_log_collection_window(date(2026, 5, 19)),
        force=False,
        send_email=True,
    )

    assert monotonic.call_count == 2
    assert result.analysis.execution_time_seconds == 3.25
    assert repository.saved[-1]["execution_time_seconds"] == 3.25


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


@pytest.mark.asyncio
async def test_sitemap_analysis_service_allows_existing_date_with_force() -> None:
    repository = FakeSitemapAnalysisRepository(exists=True)
    service = SitemapAnalysisService(
        repository=repository,
        root_sitemap_url="https://example.com/sitemap.xml",
    )

    result = await service.run_sitemap_analysis(
        analysis_date=date(2026, 5, 19),
        force=True,
        send_email=True,
    )

    assert result.analysis.status == RunStatus.SUCCEEDED.value
    assert repository.created == []
    assert repository.saved[0]["status"] == RunStatus.RUNNING.value
    assert repository.saved[-1]["status"] == RunStatus.SUCCEEDED.value
