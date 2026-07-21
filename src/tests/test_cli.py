import inspect
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from socket import gaierror
from types import TracebackType
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
import typer
from asyncpg import PostgresError  # type: ignore[import-untyped]
from click import unstyle
from llm_core.exceptions import ProviderConfigurationError, ProviderExecutionError
from pytest_mock import MockerFixture
from tortoise.exceptions import IntegrityError
from typer.testing import CliRunner

import main
import reports_cli
from cli import db as db_cli
from cli.db import makemigrations, migrate
from decorators import as_async, db
from exceptions import LogAnalysisAgentError, McpClientError, PrivateMonitoringContextError
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogAnalysisAllowedAction,
    LogAnalysisCurrentCoverage,
    LogAnalysisEvidenceMode,
    LogAnalysisFinalReport,
    LogAnalysisNextRequiredAction,
    LogAnalysisOut,
    LogAnalysisPreparedPrompt,
    LogAnalysisPromptCollection,
    LogAnalysisPromptContext,
    LogAnalysisPromptPhase,
    LogAnalysisSeverity,
    LogAnalysisWorkflowResult,
    LogCollectionWindow,
    LogWorkspace,
    McpServiceStatus,
    McpToolName,
    ProjectManifestSummary,
    SitemapAnalysisOut,
    SnapshotAccessGuidance,
    WorkflowBootstrap,
)
from tests.conftest import build_collect_logs_artifact_payload, override_settings

runner = CliRunner()


class FakeDatabaseLifespan:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeEmailDeliveryRepository:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def create(self, data: Any) -> Any:
        self.created.append(data)
        return data


def _patch_report_command_lifespan(mocker: MockerFixture) -> None:
    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)


def _patch_log_analysis_command_dependencies(
    mocker: MockerFixture,
    fake_service: object,
) -> dict[str, Any]:
    """Patch command-layer constructors while testing CLI argument handling."""

    service_calls: list[dict[str, Any]] = []
    original_service_class = main.LogAnalysisService
    email_service = AsyncMock()

    class FakeLogAnalysisRepository:
        def __init__(self) -> None:
            self.updated: list[tuple[LogAnalysisOut, dict[str, Any]]] = []

        async def update(self, analysis: LogAnalysisOut, **updates: Any) -> LogAnalysisOut:
            self.updated.append((analysis, updates))
            return analysis.model_copy(update=updates)

    class FakeLogAnalysisServiceConstructor:
        create_log_collection_window = staticmethod(
            original_service_class.create_log_collection_window
        )

        def __new__(cls, **kwargs: Any) -> Any:
            service_calls.append(kwargs)
            setattr(fake_service, "repository", kwargs["repository"])
            return fake_service

    dependencies = dict[str, Any](
        mcp_client=object(),
        agent=object(),
        llm_provider=object(),
        repository=FakeLogAnalysisRepository(),
        email_delivery_repository=FakeEmailDeliveryRepository(),
        llm_call_repository=object(),
        email_service=email_service,
        service_calls=service_calls,
    )
    dependencies["mcp_client_constructor"] = mocker.patch.object(
        main,
        "McpWorkflowClient",
        return_value=dependencies["mcp_client"],
    )
    dependencies["agent_constructor"] = mocker.patch.object(
        main,
        "MonitoringWorkflowAgent",
        return_value=dependencies["agent"],
    )
    dependencies["llm_provider_factory"] = mocker.patch.object(
        main,
        "get_llm_provider",
        return_value=dependencies["llm_provider"],
    )
    dependencies["context_loader"] = mocker.patch.object(
        main,
        "load_private_monitoring_context",
        return_value="Private Monitoring context",
    )
    dependencies["repository_constructor"] = mocker.patch.object(
        main,
        "LogAnalysisRepository",
        return_value=dependencies["repository"],
    )
    dependencies["llm_call_repository_constructor"] = mocker.patch.object(
        main,
        "LLMCallRepository",
        return_value=dependencies["llm_call_repository"],
    )
    dependencies["email_delivery_repository_constructor"] = mocker.patch.object(
        main,
        "EmailDeliveryRepository",
        return_value=dependencies["email_delivery_repository"],
    )
    dependencies["email_service_factory"] = mocker.patch.object(
        main.MonitoringEmailService,
        "create_default",
        return_value=dependencies["email_service"],
    )
    mocker.patch.object(
        main,
        "LogAnalysisService",
        FakeLogAnalysisServiceConstructor,
    )
    return dependencies


def _log_analysis_out(analysis_date: date) -> LogAnalysisOut:
    return LogAnalysisOut(
        id=1,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=analysis_date,
        status="succeeded",
        summary="Demo shop logs are healthy.",
        severity="INFO",
        key_findings=["No critical incidents found."],
        recommendations="Keep watching the backend logs.",
        trend_summary="No prior trend data was available.",
        execution_time_seconds=3.25,
    )


def _log_analysis_result(analysis_date: date) -> LogAnalysisWorkflowResult:
    workflow = WorkflowBootstrap(
        workflow_name="analyze_daily_log_bundle",
        prompt="Prompt",
        mandatory_skills=[],
        optional_skills=[],
        tools=[],
    )
    collect_logs = CollectLogsArtifact.model_validate(
        build_collect_logs_artifact_payload(
            next_step_tips=[],
            resolved_source_keys=["backend"],
        )
    )
    return LogAnalysisWorkflowResult(
        analysis=_log_analysis_out(analysis_date),
        agent_context=LogAnalysisAgentContext(
            workflow=workflow,
            collect_logs=collect_logs,
            prompt=LogAnalysisPreparedPrompt(
                system_prompt="Prompt",
                context=LogAnalysisPromptContext(
                    analysis_date=analysis_date,
                    workflow_name=workflow.workflow_name,
                    current_phase=LogAnalysisPromptPhase.FINAL_REPORT,
                    completed_steps=[
                        "analyze_daily_log_bundle",
                        "read_mandatory_skills",
                        "list_projects",
                        "collect_logs",
                    ],
                    allowed_actions=[
                        LogAnalysisAllowedAction.CALL_TOOLS,
                        LogAnalysisAllowedAction.READ_SKILLS,
                        LogAnalysisAllowedAction.FINAL_REPORT,
                    ],
                    evidence_mode=LogAnalysisEvidenceMode.CURRENT_TOOL_RESULTS_AVAILABLE,
                    current_tool_result_count=1,
                    current_coverage=LogAnalysisCurrentCoverage(),
                    next_required_action=LogAnalysisNextRequiredAction.FINAL_REPORT,
                    final_report_allowed=True,
                    available_projects=[
                        ProjectManifestSummary(
                            project_name="demo-shop",
                            project_summary="Demo shop project.",
                            source_keys=["backend"],
                        )
                    ],
                    mandatory_skills=[],
                    optional_skills=[],
                    collection=LogAnalysisPromptCollection(
                        action=McpToolName.COLLECT_LOGS,
                        workspace=LogWorkspace.WORKFLOW,
                        session_id=collect_logs.session_id,
                        projects=[],
                    ),
                    snapshot_access=SnapshotAccessGuidance(
                        workspace=LogWorkspace.WORKFLOW,
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
                        "recommendations": "string",
                        "trend_summary": "string",
                    },
                    instructions=[
                        "Use deterministic MCP snapshot tools before final report.",
                    ],
                ),
            ),
            final_report=LogAnalysisFinalReport(
                action="final_report",
                summary="Demo shop logs are healthy.",
                severity=LogAnalysisSeverity.INFO,
                severity_rationale="INFO because no service-impacting issue was found.",
                key_findings=["No critical incidents found."],
                evidence=["group_errors found no repeated backend errors."],
                coverage_gaps=["scheduler collected zero lines."],
                recommendations="Keep watching the backend logs.",
                watch_only_items=["Routine SSH brute-force traffic blocked by fail2ban."],
                trend_summary="No prior trend data was available.",
            ),
            log_window_since=datetime(2026, 5, 19, tzinfo=UTC),
            log_window_until=datetime(2026, 5, 20, tzinfo=UTC),
            llm_tokens_used=123,
            llm_cost_usd=0.02,
            llm_report_execution_time_seconds=4.32,
        ),
    )


def _stored_log_report(
    analysis_date: date = date(2026, 5, 19),
    *,
    status: str = "succeeded",
    email_sent: bool = True,
) -> LogAnalysisOut:
    return _log_analysis_out(analysis_date).model_copy(
        update={
            "status": status,
            "email_sent": email_sent,
            "mcp_artifact": {
                "collect_logs": build_collect_logs_artifact_payload(
                    resolved_source_keys=["backend", "nginx"],
                    include_unavailable_nginx=True,
                )
            },
            "mcp_collect_logs_id": "workflow/demo-shop/latest",
            "log_window_since": datetime(2026, 5, 19, tzinfo=UTC),
            "log_window_until": datetime(2026, 5, 20, tzinfo=UTC),
            "key_findings": ["No critical incidents found."],
            "evidence_fingerprints": ["nginx:http_4xx:404:/.env"],
            "coverage_snapshot": {
                "projects": [
                    {
                        "project_name": "demo-shop",
                        "sources": [
                            {"source_key": "backend", "status": "collected"},
                            {"source_key": "nginx", "status": "unavailable"},
                        ],
                    }
                ]
            },
        }
    )


def _stored_sitemap_report(
    analysis_date: date = date(2026, 5, 19),
    *,
    status: str = "succeeded",
    email_sent: bool = True,
) -> SitemapAnalysisOut:
    return SitemapAnalysisOut(
        id=7,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=analysis_date,
        status=status,
        root_sitemap_url="https://example.com/sitemap.xml",
        total_sitemaps=2,
        total_urls=42,
        issue_summary={"canonical_mismatch": 1},
        issues=[
            {
                "category": "canonical_mismatch",
                "url": "https://example.com/bad",
                "message": "Canonical points elsewhere.",
            }
        ],
        summary="One deterministic sitemap issue was found.",
        severity="WARNING",
        key_findings=["One canonical mismatch."],
        recommendations="Fix the canonical URL.",
        trend_summary="Canonical mismatch is new since the previous run.",
        execution_time_seconds=1.5,
        email_sent=email_sent,
        error_message="sitemap failed" if status == "failed" else "",
    )


def test_reports_log_list_prints_recent_reports(mocker: MockerFixture) -> None:
    _patch_report_command_lifespan(mocker)
    report = _stored_log_report(email_sent=False)

    class FakeLogAnalysisRepository:
        async def recent_reports(self, *, limit: int) -> list[LogAnalysisOut]:
            assert limit == 3
            return [report]

    mocker.patch.object(
        reports_cli,
        "LogAnalysisRepository",
        return_value=FakeLogAnalysisRepository(),
    )

    result = runner.invoke(main.app, ["reports", "log", "list", "--limit", "3"])

    assert result.exit_code == 0
    assert "Recent log-analysis reports" in result.output
    assert "2026-05-19" in result.output
    assert "INFO" in result.output
    assert "email=pending" in result.output
    assert "Demo shop logs are healthy." in result.output


def test_reports_log_show_prints_mcp_reference_hints(mocker: MockerFixture) -> None:
    _patch_report_command_lifespan(mocker)
    report = _stored_log_report()

    class FakeLogAnalysisRepository:
        async def get_by_date(self, analysis_date: date) -> LogAnalysisOut | None:
            assert analysis_date == date(2026, 5, 19)
            return report

    mocker.patch.object(
        reports_cli,
        "LogAnalysisRepository",
        return_value=FakeLogAnalysisRepository(),
    )

    result = runner.invoke(main.app, ["reports", "log", "show", "--date", "2026-05-19"])

    assert result.exit_code == 0
    assert "Log report 2026-05-19" in result.output
    assert "MCP artifact reference: workflow/demo-shop/latest" in result.output
    assert "MCP follow-up hints" in result.output
    assert "project_name=demo-shop" in result.output
    assert "source backend: collected" in result.output
    assert "source nginx: unavailable" in result.output
    assert "nginx:http_4xx:404:/.env" in result.output
    assert "MCP artifact retention" in result.output
    assert "Raw logs stay in MCP-owned artifacts" in result.output


def test_reports_sitemap_commands_support_text_and_json(mocker: MockerFixture) -> None:
    _patch_report_command_lifespan(mocker)
    report = _stored_sitemap_report()

    class FakeSitemapAnalysisRepository:
        async def recent_reports(self, *, limit: int) -> list[SitemapAnalysisOut]:
            assert limit == 5
            return [report]

        async def get_by_date(self, analysis_date: date) -> SitemapAnalysisOut | None:
            assert analysis_date == date(2026, 5, 19)
            return report

    mocker.patch.object(
        reports_cli,
        "SitemapAnalysisRepository",
        return_value=FakeSitemapAnalysisRepository(),
    )

    list_result = runner.invoke(main.app, ["reports", "sitemap", "list", "--limit", "5"])
    show_result = runner.invoke(
        main.app,
        ["reports", "sitemap", "show", "--date", "2026-05-19", "--json"],
    )
    show_text_result = runner.invoke(
        main.app,
        ["reports", "sitemap", "show", "--date", "2026-05-19"],
    )

    assert list_result.exit_code == 0
    assert "Recent sitemap-analysis reports" in list_result.output
    assert "issues=1" in list_result.output
    assert "duration=1.50s" in list_result.output
    assert "https://example.com/sitemap.xml" in list_result.output
    assert show_result.exit_code == 0
    payload = json.loads(show_result.output)
    assert payload["analysis_date"] == "2026-05-19"
    assert payload["issue_count"] == 1
    assert payload["issues"][0]["category"] == "canonical_mismatch"
    assert show_text_result.exit_code == 0
    assert "Execution time: 1.50s" in show_text_result.output
    assert "Trend: Canonical mismatch is new since the previous run." in show_text_result.output


def test_reports_log_show_json_includes_mcp_artifact_retention_notice(
    mocker: MockerFixture,
) -> None:
    _patch_report_command_lifespan(mocker)
    report = _stored_log_report()

    class FakeLogAnalysisRepository:
        async def get_by_date(self, analysis_date: date) -> LogAnalysisOut | None:
            assert analysis_date == date(2026, 5, 19)
            return report

    mocker.patch.object(
        reports_cli,
        "LogAnalysisRepository",
        return_value=FakeLogAnalysisRepository(),
    )

    result = runner.invoke(
        main.app,
        ["reports", "log", "show", "--date", "2026-05-19", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["mcp_artifact_retention_notice"] == {
        "raw_logs_owner": "mcp",
        "monitoring_app_copies_raw_logs": False,
        "reference": "workflow/demo-shop/latest",
        "message": (
            "Raw logs stay in MCP-owned artifacts and are not copied into "
            "agent-monitoring. Stored summaries and findings remain useful if "
            "the MCP artifact expires, but raw follow-up may no longer resolve."
        ),
    }


def test_reports_attention_json_lists_failed_and_unsent_runs(mocker: MockerFixture) -> None:
    _patch_report_command_lifespan(mocker)
    failed_log = _stored_log_report(
        analysis_date=date(2026, 5, 18),
        status="failed",
        email_sent=True,
    )
    unsent_sitemap = _stored_sitemap_report(email_sent=False)

    class FakeLogAnalysisRepository:
        async def failed_reports(self, *, limit: int) -> list[LogAnalysisOut]:
            assert limit == 10
            return [failed_log]

        async def unsent_emails(self, *, limit: int) -> list[LogAnalysisOut]:
            assert limit == 10
            return []

    class FakeSitemapAnalysisRepository:
        async def failed_reports(self, *, limit: int) -> list[SitemapAnalysisOut]:
            assert limit == 10
            return []

        async def unsent_emails(self, *, limit: int) -> list[SitemapAnalysisOut]:
            assert limit == 10
            return [unsent_sitemap]

    mocker.patch.object(
        reports_cli,
        "LogAnalysisRepository",
        return_value=FakeLogAnalysisRepository(),
    )
    mocker.patch.object(
        reports_cli,
        "SitemapAnalysisRepository",
        return_value=FakeSitemapAnalysisRepository(),
    )

    result = runner.invoke(main.app, ["reports", "attention", "--limit", "10", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["failed_log_reports"][0]["analysis_date"] == "2026-05-18"
    assert payload["unsent_sitemap_reports"][0]["analysis_date"] == "2026-05-19"


def test_cleanup_reports_defaults_to_dry_run(mocker: MockerFixture) -> None:
    _patch_report_command_lifespan(mocker)
    cleanup_service = AsyncMock()
    cleanup_service.cleanup_reports.return_value = {
        "retention_days": {
            "log_analyses": 30,
            "sitemap_analyses": 30,
        },
        "protected_log_history_count": 5,
        "dry_run": True,
        "counts": {
            "log_analyses": 2,
            "sitemap_analyses": 1,
        },
        "total": 3,
    }

    mocker.patch.object(
        reports_cli,
        "MonitoringCleanupService",
        return_value=cleanup_service,
    )

    result = runner.invoke(main.app, ["cleanup", "reports", "--retention-days", "30"])

    assert result.exit_code == 0
    cleanup_service.cleanup_reports.assert_awaited_once_with(
        log_retention_days=30,
        sitemap_retention_days=30,
        protected_log_history_count=5,
        dry_run=True,
    )
    assert "Cleanup reports dry run" in result.output
    assert "log_retention_days=30" in result.output
    assert "sitemap_retention_days=30" in result.output
    assert "protected_log_history=5" in result.output
    assert "log_analyses=2" in result.output
    assert "sitemap_analyses=1" in result.output
    assert "log_analysis_llm_calls" not in result.output
    assert "total=3" in result.output


def test_cleanup_reports_confirm_deletes_candidates(mocker: MockerFixture) -> None:
    _patch_report_command_lifespan(mocker)
    cleanup_service = AsyncMock()
    cleanup_service.cleanup_reports.return_value = {
        "retention_days": {
            "log_analyses": 90,
            "sitemap_analyses": 14,
        },
        "protected_log_history_count": 5,
        "dry_run": False,
        "counts": {
            "log_analyses": 1,
            "sitemap_analyses": 0,
        },
        "total": 1,
    }

    mocker.patch.object(
        reports_cli,
        "MonitoringCleanupService",
        return_value=cleanup_service,
    )

    result = runner.invoke(
        main.app,
        [
            "cleanup",
            "reports",
            "--log-retention-days",
            "90",
            "--sitemap-retention-days",
            "14",
            "--confirm",
        ],
    )

    assert result.exit_code == 0
    cleanup_service.cleanup_reports.assert_awaited_once_with(
        log_retention_days=90,
        sitemap_retention_days=14,
        protected_log_history_count=5,
        dry_run=False,
    )
    assert "Deleted cleanup candidates" in result.output
    assert "log_retention_days=90" in result.output
    assert "sitemap_retention_days=14" in result.output
    assert "log_analyses=1" in result.output
    assert "log_analysis_llm_calls" not in result.output
    assert "total=1" in result.output


def test_cleanup_reports_help_mentions_protected_history() -> None:
    result = runner.invoke(main.app, ["cleanup", "reports", "--help"])

    assert result.exit_code == 0
    assert "keeping recent successful log history" in unstyle(result.output)
    assert "protected recent" in unstyle(result.output)
    assert "successful log-analysis" in unstyle(result.output)
    assert "history" in unstyle(result.output)
    assert "--log-retention-days" in unstyle(result.output)
    assert "--sitemap-retention" in unstyle(result.output)
    assert "--protected-log-histor" in unstyle(result.output)


def _sitemap_analysis_out(analysis_date: date) -> SitemapAnalysisOut:
    return SitemapAnalysisOut(
        id=1,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=analysis_date,
        status="succeeded",
        root_sitemap_url="https://example.com/sitemap.xml",
        summary="Sitemap analysis service is ready.",
    )


def test_cli_help_lists_monitoring_commands() -> None:
    result = runner.invoke(main.app, ["--help"])

    assert result.exit_code == 0
    assert "log-analysis" in result.output
    assert "sitemap-analysis" in result.output
    assert "check-mcp" in result.output


def test_log_analysis_command_loads_mcp_workflow_bundle(
    mocker: MockerFixture,
) -> None:
    class FakeLogAnalysisService:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run_log_analysis(
            self,
            *,
            analysis_date: date,
            log_window: LogCollectionWindow,
            force: bool,
        ) -> LogAnalysisWorkflowResult:
            self.calls.append(
                {
                    "analysis_date": analysis_date,
                    "log_window": log_window.model_dump(),
                    "force": force,
                }
            )
            return _log_analysis_result(analysis_date)

    fake_service = FakeLogAnalysisService()
    dependencies = _patch_log_analysis_command_dependencies(mocker, fake_service)

    result = runner.invoke(main.app, ["log-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code == 0
    assert "Completed log-analysis report analyze_daily_log_bundle" in result.output
    assert "severity=INFO" in result.output
    assert "Summary: Demo shop logs are healthy." in result.output
    assert "Severity rationale: INFO because no service-impacting issue was found." in result.output
    assert "Key findings:" in result.output
    assert "- No critical incidents found." in result.output
    assert "Evidence:" in result.output
    assert "- group_errors found no repeated backend errors." in result.output
    assert "Coverage gaps:" in result.output
    assert "- scheduler collected zero lines." in result.output
    assert "Watch-only items:" in result.output
    assert "- Routine SSH brute-force traffic blocked by fail2ban." in result.output
    assert "Recommendations: Keep watching the backend logs." in result.output
    assert "LLM report time: 4.32s" in result.output
    assert "Execution time: 3.25s" in result.output
    assert fake_service.calls[0] == {
        "analysis_date": date(2026, 5, 19),
        "log_window": {
            "since": "2026-05-18T22:00:00Z",
            "until": "2026-05-19T22:00:00Z",
            "since_datetime": datetime(2026, 5, 18, 22, tzinfo=UTC),
            "until_datetime": datetime(2026, 5, 19, 22, tzinfo=UTC),
        },
        "force": False,
    }
    assert dependencies["llm_call_repository_constructor"].call_args.kwargs["trace_id"]
    assert (
        dependencies["service_calls"][0]["llm_call_repository"]
        is dependencies["llm_call_repository"]
    )
    dependencies["llm_provider_factory"].assert_called_once_with("gpt-5")
    assert dependencies["agent_constructor"].call_args.args[0] is dependencies["mcp_client"]
    assert (
        dependencies["agent_constructor"].call_args.kwargs["llm_provider"]
        is dependencies["llm_provider"]
    )
    assert "strong_llm_provider" not in dependencies["agent_constructor"].call_args.kwargs
    dependencies["email_service"].send_log_analysis.assert_awaited_once()
    assert dependencies["repository"].updated[0][1] == {"email_sent": True}
    delivery = dependencies["email_delivery_repository"].created[0]
    assert delivery.report_kind == "log_analysis"
    assert delivery.report_id == 1
    assert delivery.recipient_target == "log"
    assert delivery.status == "succeeded"
    assert delivery.analysis_date == date(2026, 5, 19)


def test_log_analysis_command_sends_failure_email_on_service_error(
    mocker: MockerFixture,
) -> None:
    class FakeLogAnalysisService:
        async def run_log_analysis(
            self,
            *,
            analysis_date: date,
            log_window: LogCollectionWindow,
            force: bool,
        ) -> LogAnalysisWorkflowResult:
            raise RuntimeError("MCP workflow unavailable")

    fake_service = FakeLogAnalysisService()
    dependencies = _patch_log_analysis_command_dependencies(mocker, fake_service)

    result = runner.invoke(main.app, ["log-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code != 0
    dependencies["email_service"].send_monitoring_failure.assert_awaited_once()
    failure = dependencies["email_service"].send_monitoring_failure.call_args.args[0]
    assert failure.command_name == "log_analysis"
    assert failure.analysis_date == date(2026, 5, 19)
    assert failure.error_type == "RuntimeError"
    assert failure.error_message == "MCP workflow unavailable"
    assert "RuntimeError: MCP workflow unavailable" in failure.traceback_text
    delivery = dependencies["email_delivery_repository"].created[0]
    assert delivery.report_kind == "monitoring_failure"
    assert delivery.report_id is None
    assert delivery.recipient_target == "failure"
    assert delivery.status == "succeeded"
    assert delivery.analysis_date == date(2026, 5, 19)


def test_log_analysis_command_uses_configured_mcp_timeout(
    mocker: MockerFixture,
) -> None:
    class FakeLogAnalysisService:
        async def run_log_analysis(
            self,
            *,
            analysis_date: date,
            log_window: LogCollectionWindow,
            force: bool,
        ) -> LogAnalysisWorkflowResult:
            return _log_analysis_result(analysis_date)

    fake_service = FakeLogAnalysisService()
    dependencies = _patch_log_analysis_command_dependencies(mocker, fake_service)

    result = runner.invoke(main.app, ["log-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code == 0
    assert dependencies["mcp_client_constructor"].call_args.kwargs["timeout_seconds"] == (
        main.settings.MCP_TIMEOUT_SECONDS
    )


@pytest.mark.asyncio
async def test_command_failure_email_includes_provider_status_and_message(
    mocker: MockerFixture,
) -> None:
    class FakeRateLimitError(RuntimeError):
        status_code = 429
        body = {
            "error": {
                "message": (
                    "You exceeded your current quota, please check your plan and billing details."
                ),
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        }

    email_delivery_repository = FakeEmailDeliveryRepository()
    mocker.patch.object(
        main,
        "EmailDeliveryRepository",
        return_value=email_delivery_repository,
    )
    email_service = AsyncMock()
    mocker.patch.object(main.MonitoringEmailService, "create_default", return_value=email_service)

    try:
        try:
            try:
                raise FakeRateLimitError("Error code: 429 - provider payload")
            except FakeRateLimitError as exc:
                raise ProviderExecutionError("OpenAI provider request failed") from exc
        except ProviderExecutionError as exc:
            raise LogAnalysisAgentError("OpenAI provider request failed") from exc
    except LogAnalysisAgentError as exc:
        await main._send_command_failure_email(
            command_name="log_analysis",
            analysis_date=date(2026, 6, 19),
            exc=exc,
            send_email=True,
        )

    email_service.send_monitoring_failure.assert_awaited_once()
    failure = email_service.send_monitoring_failure.call_args.args[0]
    assert "Status 429" in failure.error_message
    assert (
        "You exceeded your current quota, please check your plan and billing details."
        in failure.error_message
    )


@pytest.mark.asyncio
async def test_command_failure_email_summarizes_raw_mcp_schema_failure(
    mocker: MockerFixture,
) -> None:
    email_delivery_repository = FakeEmailDeliveryRepository()
    mocker.patch.object(
        main,
        "EmailDeliveryRepository",
        return_value=email_delivery_repository,
    )
    email_service = AsyncMock()
    mocker.patch.object(main.MonitoringEmailService, "create_default", return_value=email_service)

    mcp_error = McpClientError(
        "MCP collect_logs response did not match expected shape. Invalid fields: "
        "result.structuredContent.projects.0.provenance_diagnostics: "
        "Extra inputs are not permitted."
    )
    try:
        try:
            raise mcp_error
        except McpClientError:
            raise TypeError("expected a datetime.date or datetime.datetime instance, got 'str'")
    except TypeError as exc:
        await main._send_command_failure_email(
            command_name="log_analysis",
            analysis_date=date(2026, 6, 20),
            exc=exc,
            send_email=True,
        )

    email_service.send_monitoring_failure.assert_awaited_once()
    failure = email_service.send_monitoring_failure.call_args.args[0]
    assert failure.error_message == (
        "MCP collect_logs returned a response field this monitoring worker did not "
        "recognize: provenance_diagnostics. Update the local MCP schema contract, then "
        "rerun the job."
    )
    assert "result.structuredContent" not in failure.error_message
    assert "$4" not in failure.error_message


@pytest.mark.asyncio
async def test_command_failure_email_preserves_mcp_timeout_diagnostics(
    mocker: MockerFixture,
) -> None:
    email_delivery_repository = FakeEmailDeliveryRepository()
    mocker.patch.object(
        main,
        "EmailDeliveryRepository",
        return_value=email_delivery_repository,
    )
    email_service = AsyncMock()
    mocker.patch.object(main.MonitoringEmailService, "create_default", return_value=email_service)
    timeout = httpx.ReadTimeout("status response timed out")
    mcp_error = McpClientError(
        "MCP workflow call failed: status response timed out",
        tool_name=McpToolName.GET_LOG_COLLECTION_STATUS,
        stage="status_poll",
        session_id="workflow-session",
        timeout_seconds=90.0,
        root_cause="ReadTimeout: status response timed out",
        retry_guidance="Retry status polling with the same session ID.",
    )
    mcp_error.__cause__ = timeout

    await main._send_command_failure_email(
        command_name="log_analysis",
        analysis_date=date(2026, 7, 19),
        exc=mcp_error,
        send_email=True,
    )

    failure = email_service.send_monitoring_failure.call_args.args[0]
    assert failure.stage == "status_poll"
    assert failure.tool_name == McpToolName.GET_LOG_COLLECTION_STATUS
    assert failure.session_id == "workflow-session"
    assert failure.timeout_seconds == 90.0
    assert failure.root_cause == "ReadTimeout: status response timed out"
    assert "same session ID" in failure.retry_guidance
    assert "status response timed out" in failure.raw_diagnostics


def test_log_analysis_command_records_failed_delivery_when_report_email_fails(
    mocker: MockerFixture,
) -> None:
    class FakeLogAnalysisService:
        async def run_log_analysis(
            self,
            *,
            analysis_date: date,
            log_window: LogCollectionWindow,
            force: bool,
        ) -> LogAnalysisWorkflowResult:
            return _log_analysis_result(analysis_date)

    fake_service = FakeLogAnalysisService()
    dependencies = _patch_log_analysis_command_dependencies(mocker, fake_service)
    dependencies["email_service"].send_log_analysis.side_effect = RuntimeError("SMTP timeout")

    result = runner.invoke(main.app, ["log-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code != 0
    assert dependencies["repository"].updated == []
    delivery = dependencies["email_delivery_repository"].created[0]
    assert delivery.report_kind == "log_analysis"
    assert delivery.report_id == 1
    assert delivery.recipient_target == "log"
    assert delivery.status == "failed"
    assert delivery.error_message == "SMTP timeout"


def test_log_analysis_command_defaults_analysis_date_to_previous_local_day(
    mocker: MockerFixture,
) -> None:
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "FakeDateTime":
            return cls(2026, 5, 20, 0, 1, tzinfo=tz)

    class FakeLogAnalysisService:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run_log_analysis(
            self,
            *,
            analysis_date: date,
            log_window: LogCollectionWindow,
            force: bool,
        ) -> LogAnalysisWorkflowResult:
            self.calls.append(
                {
                    "analysis_date": analysis_date,
                    "log_window": log_window.model_dump(),
                    "force": force,
                }
            )
            return _log_analysis_result(analysis_date)

    fake_service = FakeLogAnalysisService()
    mocker.patch.object(main, "datetime", FakeDateTime)
    dependencies = _patch_log_analysis_command_dependencies(mocker, fake_service)

    result = runner.invoke(main.app, ["log-analysis"])

    assert result.exit_code == 0
    assert fake_service.calls[0]["analysis_date"] == date(2026, 5, 19)
    assert fake_service.calls[0]["log_window"] == {
        "since": "2026-05-18T22:00:00Z",
        "until": "2026-05-19T22:00:00Z",
        "since_datetime": datetime(2026, 5, 18, 22, tzinfo=UTC),
        "until_datetime": datetime(2026, 5, 19, 22, tzinfo=UTC),
    }
    assert "analysis_date=2026-05-19" in result.output
    assert dependencies["llm_call_repository_constructor"].call_args.kwargs["trace_id"]
    dependencies["email_service"].send_log_analysis.assert_awaited_once()


def test_check_mcp_command_calls_mcp_service_status(
    mocker: MockerFixture,
) -> None:
    class FakeMcpWorkflowClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_service_status(self) -> McpServiceStatus:
            self.calls.append("get_service_status")
            return McpServiceStatus(
                name="workflow-mcp",
                status="ok",
                environment="dev",
                client_type="workflow_agent",
            )

    fake_client = FakeMcpWorkflowClient()
    build_client = mocker.patch.object(
        main,
        "McpWorkflowClient",
        return_value=fake_client,
    )

    result = runner.invoke(main.app, ["check-mcp"])

    assert result.exit_code == 0
    assert fake_client.calls == ["get_service_status"]
    assert build_client.call_args.kwargs == {
        "base_url": main.settings.MCP_URL,
        "workflow_jwt": main.settings.MCP_WORKFLOW_JWT,
        "keycloak_url": main.settings.MCP_KEYCLOAK_URL,
        "keycloak_client_id": main.settings.MCP_KEYCLOAK_CLIENT_ID,
        "keycloak_client_secret": main.settings.MCP_KEYCLOAK_CLIENT_SECRET,
        "timeout_seconds": main.settings.MCP_TIMEOUT_SECONDS,
    }
    assert "MCP service is reachable" in result.output
    assert "name=workflow-mcp" in result.output
    assert "status=ok" in result.output


def test_sitemap_analysis_command_calls_sitemap_service(
    mocker: MockerFixture,
) -> None:
    class FakeSitemapAnalysisRepository:
        def __init__(self) -> None:
            self.updated: list[tuple[SitemapAnalysisOut, dict[str, Any]]] = []

        async def update(
            self,
            analysis: SitemapAnalysisOut,
            **updates: Any,
        ) -> SitemapAnalysisOut:
            self.updated.append((analysis, updates))
            return analysis.model_copy(update=updates)

    fake_repository = FakeSitemapAnalysisRepository()

    class FakeAnalysisRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(
            self,
            *,
            analysis_date: date,
            force: bool,
        ) -> SitemapAnalysisOut:
            self.calls.append(
                {
                    "analysis_date": analysis_date,
                    "force": force,
                }
            )
            return _sitemap_analysis_out(analysis_date)

    fake_runner = FakeAnalysisRunner()
    build_runner = mocker.patch.object(
        main,
        "AnalysisRunner",
        return_value=fake_runner,
    )
    llm_provider = object()
    get_llm_provider = mocker.patch.object(
        main,
        "get_llm_provider",
        return_value=llm_provider,
    )
    email_service = AsyncMock()
    email_delivery_repository = FakeEmailDeliveryRepository()
    mocker.patch.object(main, "SitemapAnalysisRepository", return_value=fake_repository)
    mocker.patch.object(
        main,
        "EmailDeliveryRepository",
        return_value=email_delivery_repository,
    )
    mocker.patch.object(main.MonitoringEmailService, "create_default", return_value=email_service)

    with override_settings(SITEMAP_PUBLIC_HOST="example.com"):
        result = runner.invoke(main.app, ["sitemap-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code == 0
    assert build_runner.call_args.kwargs["sitemap_url"] == "https://example.com/sitemap.xml"
    crawler = build_runner.call_args.kwargs["crawler"]
    assert isinstance(crawler, main.Crawler)
    assert crawler.sitemap_url == "https://example.com/sitemap.xml"
    assert crawler.sitemap_hostname == "example.com"
    assert isinstance(
        build_runner.call_args.kwargs["summary_builder"],
        main.LLMSummaryBuilder,
    )
    assert build_runner.call_args.kwargs["summary_builder"].llm_provider is llm_provider
    get_llm_provider.assert_called_once_with(main.settings.LLM_DEFAULT_MODEL)
    assert "Completed sitemap analysis" in result.output
    assert "severity=INFO" in result.output
    assert "Summary: Sitemap analysis service is ready." in result.output
    assert fake_runner.calls[0] == {
        "analysis_date": date(2026, 5, 19),
        "force": False,
    }
    email_service.send_sitemap_analysis.assert_awaited_once()
    assert fake_repository.updated[0][1] == {"email_sent": True}
    delivery = email_delivery_repository.created[0]
    assert delivery.report_kind == "sitemap_analysis"
    assert delivery.report_id == 1
    assert delivery.recipient_target == "sitemap"
    assert delivery.status == "succeeded"
    assert delivery.analysis_date == date(2026, 5, 19)


def test_sitemap_analysis_command_sends_failure_email_on_service_error(
    mocker: MockerFixture,
) -> None:
    class FakeSitemapAnalysisRepository:
        async def update(
            self,
            analysis: SitemapAnalysisOut,
            **updates: Any,
        ) -> SitemapAnalysisOut:
            return analysis.model_copy(update=updates)

    class FakeAnalysisRunner:
        async def run(
            self,
            *,
            analysis_date: date,
            force: bool,
        ) -> SitemapAnalysisOut:
            raise RuntimeError("sitemap fetch failed")

    fake_runner = FakeAnalysisRunner()
    mocker.patch.object(main, "AnalysisRunner", return_value=fake_runner)
    mocker.patch.object(main, "get_llm_provider", return_value=object())
    email_delivery_repository = FakeEmailDeliveryRepository()
    mocker.patch.object(
        main, "SitemapAnalysisRepository", return_value=FakeSitemapAnalysisRepository()
    )
    mocker.patch.object(
        main,
        "EmailDeliveryRepository",
        return_value=email_delivery_repository,
    )
    email_service = AsyncMock()
    mocker.patch.object(main.MonitoringEmailService, "create_default", return_value=email_service)

    with override_settings(SITEMAP_PUBLIC_HOST="example.com"):
        result = runner.invoke(main.app, ["sitemap-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code != 0
    email_service.send_monitoring_failure.assert_awaited_once()
    failure = email_service.send_monitoring_failure.call_args.args[0]
    assert failure.command_name == "sitemap-analysis"
    assert failure.analysis_date == date(2026, 5, 19)
    assert failure.error_type == "RuntimeError"
    assert failure.error_message == "sitemap fetch failed"
    assert "RuntimeError: sitemap fetch failed" in failure.traceback_text
    delivery = email_delivery_repository.created[0]
    assert delivery.report_kind == "monitoring_failure"
    assert delivery.report_id is None
    assert delivery.recipient_target == "failure"
    assert delivery.status == "succeeded"
    assert delivery.analysis_date == date(2026, 5, 19)


def test_sitemap_analysis_command_records_failed_delivery_when_report_email_fails(
    mocker: MockerFixture,
) -> None:
    class FakeSitemapAnalysisRepository:
        def __init__(self) -> None:
            self.updated: list[tuple[SitemapAnalysisOut, dict[str, Any]]] = []

        async def update(
            self,
            analysis: SitemapAnalysisOut,
            **updates: Any,
        ) -> SitemapAnalysisOut:
            self.updated.append((analysis, updates))
            return analysis.model_copy(update=updates)

    class FakeAnalysisRunner:
        async def run(
            self,
            *,
            analysis_date: date,
            force: bool,
        ) -> SitemapAnalysisOut:
            return _sitemap_analysis_out(analysis_date)

    fake_repository = FakeSitemapAnalysisRepository()
    email_delivery_repository = FakeEmailDeliveryRepository()
    mocker.patch.object(main, "AnalysisRunner", return_value=FakeAnalysisRunner())
    mocker.patch.object(main, "get_llm_provider", return_value=object())
    mocker.patch.object(main, "SitemapAnalysisRepository", return_value=fake_repository)
    mocker.patch.object(
        main,
        "EmailDeliveryRepository",
        return_value=email_delivery_repository,
    )
    email_service = AsyncMock()
    email_service.send_sitemap_analysis.side_effect = RuntimeError("SMTP timeout")
    mocker.patch.object(main.MonitoringEmailService, "create_default", return_value=email_service)

    with override_settings(SITEMAP_PUBLIC_HOST="example.com"):
        result = runner.invoke(main.app, ["sitemap-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code != 0
    assert fake_repository.updated == []
    delivery = email_delivery_repository.created[0]
    assert delivery.report_kind == "sitemap_analysis"
    assert delivery.report_id == 1
    assert delivery.recipient_target == "sitemap"
    assert delivery.status == "failed"
    assert delivery.error_message == "SMTP timeout"


def test_sitemap_analysis_command_exits_nonzero_for_failed_analysis_row(
    mocker: MockerFixture,
) -> None:
    class FakeSitemapAnalysisRepository:
        async def update(
            self,
            analysis: SitemapAnalysisOut,
            **updates: Any,
        ) -> SitemapAnalysisOut:
            return analysis.model_copy(update=updates)

    class FakeAnalysisRunner:
        async def run(
            self,
            *,
            analysis_date: date,
            force: bool,
        ) -> SitemapAnalysisOut:
            return _sitemap_analysis_out(analysis_date).model_copy(
                update={
                    "status": "FAILED",
                    "severity": "CRITICAL",
                    "error_message": "sitemap audit failed",
                }
            )

    fake_runner = FakeAnalysisRunner()
    mocker.patch.object(main, "AnalysisRunner", return_value=fake_runner)
    mocker.patch.object(main, "get_llm_provider", return_value=object())
    email_delivery_repository = FakeEmailDeliveryRepository()
    mocker.patch.object(
        main, "SitemapAnalysisRepository", return_value=FakeSitemapAnalysisRepository()
    )
    mocker.patch.object(
        main,
        "EmailDeliveryRepository",
        return_value=email_delivery_repository,
    )
    email_service = AsyncMock()
    mocker.patch.object(main.MonitoringEmailService, "create_default", return_value=email_service)

    with override_settings(SITEMAP_PUBLIC_HOST="example.com"):
        result = runner.invoke(main.app, ["sitemap-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code != 0
    assert "Sitemap analysis failed" in result.output
    email_service.send_monitoring_failure.assert_awaited_once()
    failure = email_service.send_monitoring_failure.call_args.args[0]
    assert failure.error_message == "sitemap audit failed"
    delivery = email_delivery_repository.created[0]
    assert delivery.report_kind == "monitoring_failure"
    assert delivery.report_id is None
    assert delivery.recipient_target == "failure"
    assert delivery.status == "succeeded"
    assert delivery.analysis_date == date(2026, 5, 19)


@pytest.mark.asyncio
async def test_sitemap_analysis_command_requires_sitemap_public_host(
    mocker: MockerFixture,
) -> None:
    sitemap_analysis_command = cast(Any, main.sitemap_analysis)
    build_runner = mocker.patch.object(main, "AnalysisRunner")
    echo = mocker.patch.object(main.typer, "echo")

    with override_settings(SITEMAP_PUBLIC_HOST=""):
        with pytest.raises(typer.Exit) as exc_info:
            await sitemap_analysis_command.__wrapped__.__wrapped__(
                analysis_date="2026-05-19",
                force=False,
                send_email=True,
            )

    assert exc_info.value.exit_code == 1
    build_runner.assert_not_called()
    echo.assert_called_once_with(
        "SITEMAP_PUBLIC_HOST is required to run sitemap analysis. "
        "Set SITEMAP_PUBLIC_HOST=example.com or SITEMAP_PUBLIC_HOST=https://example.com.",
        err=True,
    )


@pytest.mark.asyncio
async def test_sitemap_analysis_command_rejects_sitemap_url_as_public_host(
    mocker: MockerFixture,
) -> None:
    sitemap_analysis_command = cast(Any, main.sitemap_analysis)
    build_runner = mocker.patch.object(main, "AnalysisRunner")
    echo = mocker.patch.object(main.typer, "echo")

    with override_settings(SITEMAP_PUBLIC_HOST="https://example.com/sitemap.xml"):
        with pytest.raises(typer.Exit) as exc_info:
            await sitemap_analysis_command.__wrapped__.__wrapped__(
                analysis_date="2026-05-19",
                force=False,
                send_email=True,
            )

    assert exc_info.value.exit_code == 1
    build_runner.assert_not_called()
    echo.assert_called_once_with(
        "SITEMAP_PUBLIC_HOST must be a domain or origin, not a sitemap URL or path. "
        "Set SITEMAP_PUBLIC_HOST=example.com or "
        "SITEMAP_PUBLIC_HOST=https://example.com.",
        err=True,
    )


def test_typer_commands_wrap_async_callbacks() -> None:
    assert not inspect.iscoroutinefunction(main.log_analysis)
    log_analysis = cast(Any, main.log_analysis)
    sitemap_analysis = cast(Any, main.sitemap_analysis)
    check_mcp = cast(Any, main.check_mcp)
    assert inspect.iscoroutinefunction(log_analysis.__wrapped__)
    assert not inspect.iscoroutinefunction(main.sitemap_analysis)
    assert inspect.iscoroutinefunction(sitemap_analysis.__wrapped__)
    assert inspect.iscoroutinefunction(sitemap_analysis.__wrapped__.__wrapped__)
    assert not inspect.iscoroutinefunction(main.check_mcp)
    assert inspect.iscoroutinefunction(check_mcp.__wrapped__)


def test_deploy_script_exports_sitemap_public_host_setting() -> None:
    deploy_text = Path("infra/scripts/release/deploy.sh").read_text()

    assert (
        'SITEMAP_PUBLIC_HOST="${SITEMAP_PUBLIC_HOST:?SITEMAP_PUBLIC_HOST is required}"'
        in deploy_text
    )
    assert "    SITEMAP_PUBLIC_HOST \\" in deploy_text
    assert "SITEMAP_INTERNAL_BASE_URL" not in deploy_text
    assert "SITEMAP_URL" not in deploy_text


def test_prod_compose_uses_canonical_project_database_and_sitemap_host() -> None:
    compose_text = Path("docker-compose.prod.yml").read_text()

    assert compose_text.startswith("name: agent-monitoring\n")
    assert "    container_name: agent-monitoring-db\n" in compose_text
    assert (
        "      SITEMAP_PUBLIC_HOST: " "${SITEMAP_PUBLIC_HOST:?SITEMAP_PUBLIC_HOST is required}\n"
    ) in compose_text
    assert "agent-monitoring-prod" not in compose_text


def test_prod_scripts_override_legacy_compose_project_name() -> None:
    for script_path in (
        "infra/scripts/release/deploy.sh",
        "infra/scripts/db_backup/backup_db.sh",
        "infra/scripts/db_backup/restore_db.sh",
    ):
        script_text = Path(script_path).read_text()
        assert 'COMPOSE_PROJECT_NAME="$(get_compose_project_name "$ENVIRONMENT")"' in script_text


def test_prod_deploy_does_not_run_legacy_transition() -> None:
    deploy_text = Path("infra/scripts/release/deploy.sh").read_text()

    assert "transition_legacy_prod_stack" not in deploy_text
    assert not Path("infra/scripts/release/transition_legacy_prod_stack.sh").exists()


def test_release_script_requires_and_exports_sitemap_public_host() -> None:
    release_text = Path("infra/scripts/release/release.sh").read_text()

    assert (
        'SITEMAP_PUBLIC_HOST="${SITEMAP_PUBLIC_HOST:?SITEMAP_PUBLIC_HOST is required}"'
        in release_text
    )
    assert "export ENVIRONMENT TAG EMERGENCY SITEMAP_PUBLIC_HOST" in release_text


def test_release_script_runs_build_then_deploy() -> None:
    release_text = Path("infra/scripts/release/release.sh").read_text()

    assert '"$SCRIPT_DIR/build.sh"' in release_text
    assert '"$SCRIPT_DIR/deploy.sh"' in release_text


def test_release_script_accepts_emergency_flag_for_dirty_tree_builds() -> None:
    release_text = Path("infra/scripts/release/release.sh").read_text()
    build_text = Path("infra/scripts/release/build.sh").read_text()

    assert "--emergency)" in release_text
    assert 'EMERGENCY="true"' in release_text
    assert "export ENVIRONMENT TAG EMERGENCY" in release_text
    assert "--emergency)" in build_text
    assert 'EMERGENCY="true"' in build_text


def test_deploy_script_records_current_tag_without_running_monitoring_command() -> None:
    deploy_text = Path("infra/scripts/release/deploy.sh").read_text()

    assert "MONITORING_COMMAND" not in deploy_text
    assert 'deploy_step "🏷️" 8 8 "Record deployed tag"' in deploy_text
    assert 'printf "%s\\n" "$TAG" > "$STATE_DIR/current_tag"' in deploy_text
    assert 'docker compose "${COMPOSE_ARGS[@]}" run --rm app migrate' in deploy_text


def test_release_scripts_use_shared_python_state_dir_resolver() -> None:
    utils_text = Path("infra/scripts/utils.sh").read_text()

    assert "from cli.utils import get_state_dir" in utils_text
    assert "get_state_dir(sys.argv[1], project_dir=Path(sys.argv[2]))" in utils_text
    assert 'local preferred="/var/lib/agent-monitoring/$environment"' not in utils_text


def test_as_async_runs_coroutine_function() -> None:
    calls: list[str] = []

    @as_async()
    async def command(name: str) -> str:
        calls.append(name)
        return name.upper()

    assert command("monitoring") == "MONITORING"
    assert calls == ["monitoring"]


def test_db_decorator_runs_coroutine_inside_database_lifespan(
    mocker: MockerFixture,
) -> None:
    calls: list[str] = []

    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            calls.append("enter")

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            calls.append("exit")

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    @as_async()
    @db
    async def command(name: str) -> str:
        calls.append(name)
        return name.upper()

    assert command("monitoring") == "MONITORING"
    assert calls == ["enter", "monitoring", "exit"]


def test_db_decorator_can_be_called_as_factory(
    mocker: MockerFixture,
) -> None:
    calls: list[str] = []

    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            calls.append("enter")

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            calls.append("exit")

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    @as_async()
    @db()
    async def command() -> str:
        calls.append("inside")
        return "done"

    assert command() == "done"
    assert calls == ["enter", "inside", "exit"]


def test_db_decorator_formats_database_connection_errors(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        raise gaierror("nodename nor servname provided, or not known")

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "Database connection failed" in output
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 1
    assert "Traceback" not in output


def test_db_decorator_does_not_label_integrity_errors_as_connection_errors(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        postgres_error = PostgresError.new(
            {
                "C": "23505",
                "M": "duplicate key value violates unique constraint",
                "n": "email_deliveries_pkey",
            }
        )
        raise IntegrityError(postgres_error) from postgres_error

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "Database integrity error" in output
    assert "duplicate key value violates unique constraint" in output
    assert "Database connection failed" not in output
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 1
    assert "Traceback" not in output


def test_db_decorator_maps_log_analysis_primary_key_conflict_to_force_retry_exit(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        postgres_error = PostgresError.new(
            {
                "C": "23505",
                "M": "duplicate key value violates unique constraint",
                "n": "log_analyses_pkey",
            }
        )
        raise IntegrityError(postgres_error) from postgres_error

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 75
    assert "Database integrity error" in output
    assert "duplicate key value violates unique constraint" in output
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 75
    assert "Traceback" not in output


@pytest.mark.parametrize("error_kind", ["impostor", "wrong_sqlstate"])
def test_db_decorator_rejects_non_matching_force_retry_causes(
    mocker: MockerFixture,
    error_kind: str,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    class ImpostorUniqueViolation(RuntimeError):
        sqlstate = "23505"
        constraint_name = "log_analyses_pkey"

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        if error_kind == "impostor":
            cause: BaseException = ImpostorUniqueViolation("not a PostgreSQL error")
        else:
            cause = PostgresError.new(
                {
                    "C": "23503",
                    "M": "foreign key constraint violation",
                    "n": "log_analyses_pkey",
                }
            )
        raise IntegrityError(cause) from cause

    result = runner.invoke(app)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 1


def test_db_decorator_formats_mcp_client_errors(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        raise McpClientError(
            "MCP workflow call failed: All connection attempts failed",
            mcp_url="http://127.0.0.1:8001/mcp",
            tool_name="analyze_daily_log_bundle",
            hint=(
                "Check MCP_URL and whether the MCP server is running. "
                "For Docker Compose commands, remember that localhost means the "
                "monitoring container, not your host."
            ),
        )

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "MCP call failed" in output
    assert "analyze_daily_log_bundle" in output
    assert "http://127.0.0.1:8001/mcp" in output
    assert "connection attempts failed" in output
    assert "Check MCP_URL" in output
    assert "server is" in output
    assert "running" in output
    assert "Docker Compose" in output
    assert "means the" in output
    assert "monitoring container" in output
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 1
    assert "Traceback" not in output


def test_db_decorator_does_not_add_connectivity_hint_to_mcp_validation_errors(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        raise McpClientError(
            (
                "MCP collect_logs response did not match expected shape. "
                "Invalid fields: result.structuredContent.projects.0.sources: Field required."
            ),
            mcp_url="http://127.0.0.1:8001/mcp",
            tool_name="collect_logs",
        )

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "Invalid fields" in output
    assert "result.structuredContent.projects.0.sources" in output
    assert "Check MCP_URL" not in output
    assert "MCP_WORKFLOW_JWT" not in output
    assert "MCP server is running" not in output


def test_db_decorator_preserves_mcp_project_error_message_without_connectivity_hint(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        raise McpClientError(
            (
                "MCP collect_logs error: Unknown project 'demo-shop'. "
                "No persisted manifest was found for that project. "
                "Retry tips: Call list_projects to discover available projects."
            ),
            mcp_url="http://127.0.0.1:8001/mcp",
            tool_name="collect_logs",
        )

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "MCP call failed" in output
    assert "collect_logs" in output
    assert "Unknown project 'demo-shop'" in output
    assert "No persisted manifest" in output
    assert "was found for that project" in output
    assert "Call list_projects" in output
    assert "Check MCP_URL" not in output
    assert "MCP_WORKFLOW_JWT" not in output
    assert "MCP server is running" not in output


def test_db_decorator_formats_llm_provider_configuration_errors(
    mocker: MockerFixture,
) -> None:
    """Provider config failures should become actionable CLI errors.

    The DB decorator wraps command execution for DB-backed commands. This test
    verifies that an LLM provider configuration exception is rendered as a
    concise Click/Typer error with the expected environment-variable guidance,
    instead of leaking a traceback to the operator.
    """

    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        raise ProviderConfigurationError("OpenAI API key is required when no client is injected")

    result = runner.invoke(app)
    output = unstyle(result.output)
    normalized_output = " ".join(output.split())

    assert result.exit_code == 1
    assert "LLM provider configuration failed" in normalized_output
    assert "OpenAI" in normalized_output
    assert "API" in normalized_output
    assert "key is required" in normalized_output
    assert "OPENAI_API_KEY" in normalized_output
    assert "OPEN_API_KEY" in normalized_output
    assert "Traceback" not in output


def test_db_decorator_formats_private_monitoring_context_errors(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        raise PrivateMonitoringContextError(
            "Project context prompt file is required but was not found: "
            "/app/private/vps_monitoring_context.md",
            context_path="/app/private/vps_monitoring_context.md",
        )

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "Project context prompt is not configured" in output
    assert "/app/private/vps_monitoring_context.md" in output
    assert "PROJECT_CONTEXT_PROMPT_PATH" in output
    assert "private/vps_monitoring_context.md" in output
    assert "Traceback" not in output


def test_db_decorator_formats_llm_provider_execution_error_cause(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        try:
            raise RuntimeError("HTTP 400: unsupported parameter 'temperature'")
        except RuntimeError as exc:
            raise ProviderExecutionError("OpenAI provider request failed") from exc

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "LLM provider request failed" in output
    assert "OpenAI provider request failed" in output
    assert "RuntimeError" in output
    assert "unsupported parameter 'temperature'" in output
    assert "Traceback" not in output


def test_db_decorator_formats_log_analysis_agent_error_cause(
    mocker: MockerFixture,
) -> None:
    class FakeDatabaseLifespan:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def fake_database_lifespan() -> FakeDatabaseLifespan:
        return FakeDatabaseLifespan()

    mocker.patch("decorators.database_lifespan", new=fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        try:
            try:
                raise RuntimeError("Rate limit reached for gpt-4.1-mini")
            except RuntimeError as exc:
                raise ProviderExecutionError("OpenAI provider request failed") from exc
        except ProviderExecutionError as exc:
            raise LogAnalysisAgentError("OpenAI provider request failed") from exc

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "Log-analysis workflow failed" in output
    assert "OpenAI provider request failed" in output
    assert "ProviderExecutionError" in output
    assert "Rate limit reached for gpt-4.1-mini" in output
    assert "Traceback" not in output


def test_makemigrations_runs_aerich_migrate_and_numbers_file(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], bool, bool]] = []
    migrations_dir = tmp_path / "migrations" / "models"
    migrations_dir.mkdir(parents=True)
    generated = migrations_dir / "0_20260519230000_add_models.py"

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, capture_output, check))
        generated.write_text("migration")
        return subprocess.CompletedProcess(args, 0, stdout="generated\n", stderr="")

    mocker.patch("cli.db.subprocess.run", new=fake_run)
    mocker.patch("cli.db.MIGRATIONS_DIR", migrations_dir)

    result = db_cli._run_makemigrations(["add_models"])

    assert result == 0
    assert calls[0][0] == ["aerich", "migrate", "--offline", "--name", "add_models"]
    assert calls[0][1] is True
    assert calls[0][2] is True
    assert capsys.readouterr().out == "generated\n"
    assert not generated.exists()
    assert (migrations_dir / "001_add_models.py").read_text() == "migration"


def test_makemigrations_uses_next_number_for_existing_migrations(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    migrations_dir = tmp_path / "migrations" / "models"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_initial_schema.py").write_text("initial")
    generated = migrations_dir / "0_20260519230000_add_email.py"

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        generated.write_text("migration")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    mocker.patch("cli.db.subprocess.run", new=fake_run)
    mocker.patch("cli.db.MIGRATIONS_DIR", migrations_dir)

    result = db_cli._run_makemigrations(["add_email"])

    assert result == 0
    assert calls == [["aerich", "migrate", "--offline", "--name", "add_email"]]
    assert not generated.exists()
    assert (migrations_dir / "002_add_email.py").read_text() == "migration"


def test_makemigrations_requires_positional_migration_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = db_cli._run_makemigrations([])

    assert result == 2
    assert "Usage: makemigrations <migration_name>" in capsys.readouterr().err


def test_makemigrations_initializes_migration_folder_when_required(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], bool, bool]] = []
    migrations_dir = tmp_path / "migrations" / "models"
    migrations_dir.mkdir(parents=True)
    generated = migrations_dir / "0_20260519230000_initial_schema.py"

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, capture_output, check))
        if args == ["aerich", "migrate", "--offline", "--name", "initial_schema"]:
            raise subprocess.CalledProcessError(
                1,
                args,
                output="",
                stderr=f"Error: {db_cli.INIT_MIGRATIONS_REQUIRED_MESSAGES[0]}\n",
            )
        if args == ["aerich", "init-migrations"]:
            generated.write_text("migration")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    mocker.patch("cli.db.subprocess.run", new=fake_run)
    mocker.patch("cli.db.MIGRATIONS_DIR", migrations_dir)

    result = db_cli._run_makemigrations(["initial_schema"])

    assert result == 0
    assert calls == [
        (["aerich", "migrate", "--offline", "--name", "initial_schema"], True, True),
        (["aerich", "init-migrations"], False, False),
    ]
    assert (migrations_dir / "001_initial_schema.py").exists()


def test_makemigrations_script_exits_with_makemigrations_result(
    mocker: MockerFixture,
) -> None:
    run_migrations = mocker.patch("cli.db._run_makemigrations", return_value=7)
    mocker.patch("cli.db.sys.argv", ["makemigrations", "add_models"])

    try:
        makemigrations()
    except SystemExit as error:
        assert error.code == 7
    else:
        raise AssertionError("makemigrations should exit")

    run_migrations.assert_called_once_with(["add_models"])


def test_migrate_script_runs_aerich_upgrade(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], bool, bool]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, capture_output, check))
        return subprocess.CompletedProcess(args, 0)

    mocker.patch("cli.db.subprocess.run", new=fake_run)
    mocker.patch("cli.db.sys.argv", ["migrate", "--fake"])

    mocker.patch.dict("os.environ", {"TAG": "", "STATE_DIR": str(tmp_path / "missing-prod")})
    with override_settings(ENVIRONMENT="dev"):
        try:
            migrate()
        except SystemExit as error:
            assert error.code == 0
        else:
            raise AssertionError("migrate should exit")

    assert calls[0][0] == ["aerich", "upgrade", "--fake"]
    assert calls[0][1] is True
    assert calls[0][2] is False


def test_migrate_script_runs_in_compose_for_deployed_prod(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "prod"
    state_dir.mkdir()
    (state_dir / "current_tag").write_text("v0.1.1\n", encoding="utf-8")
    run = mocker.patch(
        "cli.utils.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )

    mocker.patch.dict("os.environ", {"TAG": "", "STATE_DIR": str(state_dir)})
    with override_settings(ENVIRONMENT="dev"):
        result = db_cli._run_migrate(["--fake"])

    assert result == 0
    assert run.call_args.args[0] == [
        "env",
        "TAG=v0.1.1",
        "COMPOSE_PROJECT_NAME=agent-monitoring",
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "migrate",
        "--fake",
    ]


def test_makemigrations_script_bridges_to_prod_compose_with_extra_args(
    mocker: MockerFixture,
) -> None:
    run = mocker.patch(
        "cli.utils.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )

    mocker.patch.dict("os.environ", {"TAG": "v0.1.1"})
    result = db_cli._run_makemigrations(["add_email_deliveries"])

    assert result == 0
    assert run.call_args.args[0] == [
        "env",
        "TAG=v0.1.1",
        "COMPOSE_PROJECT_NAME=agent-monitoring",
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "makemigrations",
        "add_email_deliveries",
    ]


def test_migrate_script_runs_aerich_inside_container(mocker: MockerFixture) -> None:
    calls: list[list[str]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    mocker.patch("cli.utils.is_running_in_container", return_value=True)
    mocker.patch("cli.db.subprocess.run", new=fake_run)

    mocker.patch.dict("os.environ", {"TAG": "v0.1.1"})
    with override_settings(ENVIRONMENT="prod"):
        result = db_cli._run_migrate([])

    assert result == 0
    assert calls == [["aerich", "upgrade"]]


def test_migrate_script_prints_old_format_guidance(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="RuntimeError: Old format of migration file detected\n",
        )

    mocker.patch("cli.db.subprocess.run", new=fake_run)
    mocker.patch("cli.db.sys.argv", ["migrate"])

    mocker.patch.dict("os.environ", {"TAG": "", "STATE_DIR": str(tmp_path / "missing-prod")})
    with override_settings(ENVIRONMENT="dev"):
        with pytest.raises(SystemExit) as error:
            migrate()

    assert error.value.code == 1
    output = capsys.readouterr()
    assert "Database migration failed." in output.err
    assert "Aerich detected an old-format migration file." in output.err
    assert "uv run aerich fix-migrations" in output.err
    assert "Traceback" not in output.err


def test_migrate_script_prints_not_null_guidance(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr=(
                'asyncpg.exceptions.NotNullViolationError: column "fingerprints" '
                'of relation "log_analyses" contains null values\n'
            ),
        )

    mocker.patch("cli.db.subprocess.run", new=fake_run)
    mocker.patch("cli.db.sys.argv", ["migrate"])

    mocker.patch.dict("os.environ", {"TAG": "", "STATE_DIR": str(tmp_path / "missing-prod")})
    with override_settings(ENVIRONMENT="dev"):
        with pytest.raises(SystemExit) as error:
            migrate()

    assert error.value.code == 1
    output = capsys.readouterr()
    assert "Database migration failed." in output.err
    assert "tried to add a NOT NULL column" in output.err
    assert "rename-style migration" in output.err
    assert "Traceback" not in output.err


def test_migrate_script_replays_generic_aerich_failure(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr='asyncpg.exceptions.DuplicateTableError: relation "email_deliveries" exists\n',
        )

    mocker.patch("cli.db.subprocess.run", new=fake_run)
    mocker.patch("cli.db.sys.argv", ["migrate"])

    mocker.patch.dict("os.environ", {"TAG": "", "STATE_DIR": str(tmp_path / "missing-prod")})
    with override_settings(ENVIRONMENT="dev"):
        with pytest.raises(SystemExit) as error:
            migrate()

    assert error.value.code == 1
    output = capsys.readouterr()
    assert "Database migration failed." in output.err
    assert 'relation "email_deliveries" exists' in output.err
    assert "Aerich output above contains the migration failure details." in output.err
    assert "Run `uv run migrate` directly" not in output.err


def test_migrate_script_explains_host_side_connection_refused(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="ConnectionRefusedError: [Errno 111] Connect call failed ('127.0.0.1', 5438)\n",
        )

    mocker.patch("cli.db.subprocess.run", new=fake_run)
    mocker.patch("cli.db.sys.argv", ["migrate"])

    mocker.patch.dict("os.environ", {"TAG": "", "STATE_DIR": str(tmp_path / "missing-prod")})
    with override_settings(ENVIRONMENT="dev"):
        with pytest.raises(SystemExit) as error:
            migrate()

    assert error.value.code == 1
    output = capsys.readouterr()
    assert "Database migration failed." in output.err
    assert "could not connect to the configured database host" in output.err
    assert "127.0.0.1:5438" in output.err
    assert "doppler run -- uv run migrate" in output.err
