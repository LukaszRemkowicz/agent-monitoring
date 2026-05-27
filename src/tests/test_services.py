from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime
from unittest.mock import ANY

import pytest
from llm_core.protocols import LLMProvider
from llm_core.providers.mock import MockProvider
from pytest_mock import MockerFixture

from agents import MonitoringWorkflowAgent
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
from services.log_analyse import LogAnalysisService
from services.sitemap import (
    AnalysisRunner,
    LLMSummaryBuilder,
    SitemapAuditReport,
    SitemapIssue,
)
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
        self.received_historical_context: str = ""

    async def run_log_analysis(
        self,
        *,
        analysis_date: date,
        log_window: LogCollectionWindow,
        historical_context: str = "",
    ) -> LogAnalysisAgentContext:
        self.calls += 1
        self.received_historical_context = historical_context
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
        historical_context: str = "",
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
        super().__init__()
        self._has_existing = exists
        self.created: list[dict[str, object]] = []
        self.saved: list[dict[str, object]] = []
        self.last_5_days_calls: list[date] = []
        self._last_5_days: list[LogAnalysisOut] = []

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

    async def last_5_days(self, analysis_date: date) -> list[LogAnalysisOut]:
        self.last_5_days_calls.append(analysis_date)
        return self._last_5_days

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

    async def update_or_create(
        self,
        *,
        existing: SitemapAnalysisOut | None,
        data: SitemapAnalysisIn,
    ) -> SitemapAnalysisOut:
        if existing is None:
            return await self.create(data)
        return await self.update(existing, **data.model_dump(exclude={"analysis_date"}))


class FakeCrawler:
    def __init__(self, report: SitemapAuditReport) -> None:
        self.report = report
        self.calls = 0

    async def audit(self) -> SitemapAuditReport:
        self.calls += 1
        return self.report

    def summarize_issues(self, issues: Iterable[SitemapIssue]) -> dict[str, int]:
        return dict(sorted(Counter(issue.category.value for issue in issues).items()))


class FailingCrawler:
    async def audit(self) -> SitemapAuditReport:
        raise RuntimeError("sitemap preparation failed")

    def summarize_issues(self, issues: Iterable[SitemapIssue]) -> dict[str, int]:
        return {}


class FakeLLMSummaryBuilder(LLMSummaryBuilder):
    def __init__(self) -> None:
        self.calls: list[tuple[SitemapAuditReport, dict[str, int]]] = []

    def summarize(
        self,
        report: SitemapAuditReport,
        issue_summary: dict[str, int],
    ) -> dict[str, object]:
        self.calls.append((report, issue_summary))
        return {
            "summary": "Sitemap summary service result.",
            "severity": "WARNING",
            "key_findings": ["Summary service was called."],
            "recommendations": "Review summary service output.",
            "trend_summary": "Trend from summary service.",
        }


@pytest.mark.asyncio
async def test_log_analysis_service_loads_workflow_bundle() -> None:
    agent = FakeWorkflowAgent()
    repository = FakeLogAnalysisRepository()
    service = LogAnalysisService(
        agent=agent,
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
    assert repository.last_5_days_calls == [date(2026, 5, 19)]
    assert agent.received_historical_context == ""
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
async def test_log_analysis_service_passes_last_5_days_to_agent() -> None:
    agent = FakeWorkflowAgent()
    repository = FakeLogAnalysisRepository()
    historical_run = LogAnalysisOut(
        id=7,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status=RunStatus.SUCCEEDED,
        summary="Previous run saw scanner noise only.",
        severity="INFO",
        key_findings=["No service impact."],
        recommendations="No action needed.",
        trend_summary="Scanner noise was stable.",
    )
    repository._last_5_days = [historical_run]
    service = LogAnalysisService(
        agent=agent,
        repository=repository,
    )

    await service.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogAnalysisService.create_log_collection_window(date(2026, 5, 19)),
        force=False,
        send_email=True,
    )

    assert repository.last_5_days_calls == [date(2026, 5, 19)]
    assert agent.received_historical_context == (
        "## 2026-05-18 — Severity: INFO\n"
        "Summary: Previous run saw scanner noise only.\n"
        "Key findings: ['No service impact.']\n"
        "Recommendations: No action needed."
    )


@pytest.mark.asyncio
async def test_log_analysis_service_records_failure_state(mocker: MockerFixture) -> None:
    repository = FakeLogAnalysisRepository()
    error_mock = mocker.patch("services.log_analyse.logger.error")
    service = LogAnalysisService(
        agent=FailingWorkflowAgent(),
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
    error_mock.assert_called_once_with(
        "log-analysis workflow failed",
        extra={
            "event": "log_analysis_workflow_failed",
            "analysis_date": "2026-05-19",
            "failure_stage": "log_analysis",
            "execution_time_seconds": ANY,
            "error": "MCP unavailable",
        },
    )


@pytest.mark.asyncio
async def test_log_analysis_service_blocks_existing_date_without_force() -> None:
    agent = FakeWorkflowAgent()
    service = LogAnalysisService(
        agent=agent,
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
async def test_log_analysis_service_records_execution_time(
    mocker: MockerFixture,
) -> None:
    agent = FakeWorkflowAgent()
    repository = FakeLogAnalysisRepository()
    service = LogAnalysisService(
        agent=agent,
        repository=repository,
    )
    monotonic = mocker.patch("services.log_analyse.monotonic", side_effect=[100.0, 103.25])

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
    crawler = FakeCrawler(
        SitemapAuditReport(
            root_sitemap_url="https://example.com/sitemap.xml",
            total_sitemaps=1,
            total_urls=2,
            issues=[],
        )
    )
    summary_builder = FakeLLMSummaryBuilder()
    runner = AnalysisRunner(
        repository=repository,
        sitemap_url="https://example.com/sitemap.xml",
        crawler=crawler,
        summary_builder=summary_builder,
    )

    result = await runner.run(
        analysis_date=date(2026, 5, 19),
        force=False,
    )

    assert result.analysis_date == repository.created[0]["analysis_date"]
    assert repository.created[0]["analysis_date"] == date(2026, 5, 19)
    assert repository.created[0]["root_sitemap_url"] == "https://example.com/sitemap.xml"
    assert repository.created[0]["status"] == RunStatus.SUCCEEDED.value
    assert repository.created[0]["summary"] == "Sitemap summary service result."
    assert crawler.calls == 1
    assert len(summary_builder.calls) == 1
    assert summary_builder.calls[0][1] == {}
    assert result.status == RunStatus.SUCCEEDED.value
    assert repository.created[0]["total_sitemaps"] == 1
    assert repository.created[0]["total_urls"] == 2
    assert repository.created[0]["issue_summary"] == {}
    assert repository.created[0]["issues"] == []
    assert repository.created[0]["severity"] == "WARNING"
    assert repository.saved == []


@pytest.mark.asyncio
async def test_sitemap_analysis_service_records_failure_state(mocker: MockerFixture) -> None:
    repository = FakeSitemapAnalysisRepository()
    error_mock = mocker.patch("services.sitemap.logger.error")
    runner = AnalysisRunner(
        repository=repository,
        sitemap_url="https://example.com/sitemap.xml",
        crawler=FailingCrawler(),
        summary_builder=FakeLLMSummaryBuilder(),
    )

    result = await runner.run(
        analysis_date=date(2026, 5, 19),
        force=False,
    )

    assert repository.created[0]["status"] == RunStatus.FAILED.value
    assert result.status == RunStatus.FAILED.value
    assert repository.created[0]["failure_stage"] == "sitemap_analysis"
    assert result.failure_stage == "sitemap_analysis"
    assert repository.created[0]["error_message"] == "sitemap preparation failed"
    assert result.error_message == "sitemap preparation failed"
    assert repository.saved == []
    error_mock.assert_called_once_with(
        "sitemap-analysis workflow failed",
        extra={
            "event": "sitemap_analysis_workflow_failed",
            "analysis_date": "2026-05-19",
            "failure_stage": "sitemap_analysis",
            "execution_time_seconds": ANY,
            "error": "sitemap preparation failed",
        },
    )


@pytest.mark.asyncio
async def test_sitemap_analysis_service_blocks_existing_date_without_force() -> None:
    repository = FakeSitemapAnalysisRepository(exists=True)
    crawler = FakeCrawler(
        SitemapAuditReport(
            root_sitemap_url="https://example.com/sitemap.xml",
            total_sitemaps=0,
            total_urls=0,
            issues=[],
        )
    )
    runner = AnalysisRunner(
        repository=repository,
        sitemap_url="https://example.com/sitemap.xml",
        crawler=crawler,
        summary_builder=FakeLLMSummaryBuilder(),
    )

    result = await runner.run(
        analysis_date=date(2026, 5, 19),
        force=False,
    )

    assert repository.created == []
    assert repository.saved == []
    assert crawler.calls == 0
    assert result.status == RunStatus.SUCCEEDED.value
    assert result.summary == "Existing analysis."
    assert result.failure_stage is None
    assert result.error_message == ""


@pytest.mark.asyncio
async def test_sitemap_analysis_service_allows_existing_date_with_force() -> None:
    repository = FakeSitemapAnalysisRepository(exists=True)
    crawler = FakeCrawler(
        SitemapAuditReport(
            root_sitemap_url="https://example.com/sitemap.xml",
            total_sitemaps=1,
            total_urls=1,
            issues=[],
        )
    )
    runner = AnalysisRunner(
        repository=repository,
        sitemap_url="https://example.com/sitemap.xml",
        crawler=crawler,
        summary_builder=FakeLLMSummaryBuilder(),
    )

    result = await runner.run(
        analysis_date=date(2026, 5, 19),
        force=True,
    )

    assert result.status == RunStatus.SUCCEEDED.value
    assert repository.created == []
    assert crawler.calls == 1
    assert len(repository.saved) == 1
    assert repository.saved[0]["status"] == RunStatus.SUCCEEDED.value
    assert repository.saved[0]["summary"] == "Sitemap summary service result."
