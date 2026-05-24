import inspect
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from socket import gaierror
from types import TracebackType
from typing import Any, cast

import pytest
import typer
from click import unstyle
from llm_core.exceptions import ProviderConfigurationError, ProviderExecutionError
from pytest_mock import MockerFixture
from tortoise.exceptions import IntegrityError
from typer.testing import CliRunner

import main
from db import cli as db_cli
from db.cli import makemigrations, migrate
from decorators import as_async, db
from exceptions import McpClientError, PrivateMonitoringContextError
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogAnalysisFinalReport,
    LogAnalysisOut,
    LogAnalysisPreparedPrompt,
    LogAnalysisPromptContext,
    LogAnalysisWorkflowResult,
    LogCollectionWindow,
    McpServiceStatus,
    ProjectManifestSummary,
    SitemapAnalysisOut,
    SitemapAnalysisWorkflowResult,
    SnapshotAccessGuidance,
    WorkflowBootstrap,
)
from tests.conftest import build_collect_logs_artifact_payload

runner = CliRunner()


def _log_analysis_out(analysis_date: date) -> LogAnalysisOut:
    return LogAnalysisOut(
        id=1,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=analysis_date,
        status="succeeded",
        summary="Landingpage logs are healthy.",
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
                    current_phase="final_report",
                    completed_steps=[
                        "analyze_daily_log_bundle",
                        "read_mandatory_skills",
                        "list_projects",
                        "collect_logs",
                    ],
                    allowed_actions=["call_tools", "read_skills", "final_report"],
                    next_required_action="final_report",
                    final_report_allowed=True,
                    available_projects=[
                        ProjectManifestSummary(
                            project_name="landingpage",
                            project_summary="Landingpage project.",
                            source_keys=["backend"],
                        )
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
                summary="Landingpage logs are healthy.",
                severity="INFO",
                severity_rationale="INFO because no service-impacting issue was found.",
                key_findings=["No critical incidents found."],
                evidence=["group_errors found no repeated backend errors."],
                coverage_gaps=["celery_beat collected zero lines."],
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
            send_email: bool,
        ) -> LogAnalysisWorkflowResult:
            self.calls.append(
                {
                    "analysis_date": analysis_date,
                    "log_window": log_window.model_dump(),
                    "force": force,
                    "send_email": send_email,
                }
            )
            return _log_analysis_result(analysis_date)

    fake_service = FakeLogAnalysisService()
    mocker.patch.object(
        main.LogAnalysisService,
        "create_default",
        return_value=fake_service,
    )

    result = runner.invoke(main.app, ["log-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code == 0
    assert "Completed log-analysis report analyze_daily_log_bundle" in result.output
    assert "severity=INFO" in result.output
    assert "Summary: Landingpage logs are healthy." in result.output
    assert "Severity rationale: INFO because no service-impacting issue was found." in result.output
    assert "Key findings:" in result.output
    assert "- No critical incidents found." in result.output
    assert "Evidence:" in result.output
    assert "- group_errors found no repeated backend errors." in result.output
    assert "Coverage gaps:" in result.output
    assert "- celery_beat collected zero lines." in result.output
    assert "Watch-only items:" in result.output
    assert "- Routine SSH brute-force traffic blocked by fail2ban." in result.output
    assert "Recommendations: Keep watching the backend logs." in result.output
    assert "LLM report time: 4.32s" in result.output
    assert "Execution time: 3.25s" in result.output
    assert fake_service.calls[0] == {
        "analysis_date": date(2026, 5, 19),
        "log_window": {
            "since": "2026-05-19T00:00:00Z",
            "until": "2026-05-20T00:00:00Z",
            "since_datetime": datetime(2026, 5, 19, tzinfo=UTC),
            "until_datetime": datetime(2026, 5, 20, tzinfo=UTC),
        },
        "force": False,
        "send_email": True,
    }


def test_log_analysis_command_defaults_analysis_date_to_today(
    mocker: MockerFixture,
) -> None:
    class FakeDate(date):
        @classmethod
        def today(cls) -> "FakeDate":
            return cls(2026, 5, 20)

    class FakeLogAnalysisService:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run_log_analysis(
            self,
            *,
            analysis_date: date,
            log_window: LogCollectionWindow,
            force: bool,
            send_email: bool,
        ) -> LogAnalysisWorkflowResult:
            self.calls.append(
                {
                    "analysis_date": analysis_date,
                    "log_window": log_window.model_dump(),
                    "force": force,
                    "send_email": send_email,
                }
            )
            return _log_analysis_result(analysis_date)

    fake_service = FakeLogAnalysisService()
    mocker.patch.object(main, "date", FakeDate)
    mocker.patch.object(
        main.LogAnalysisService,
        "create_default",
        return_value=fake_service,
    )

    result = runner.invoke(main.app, ["log-analysis"])

    assert result.exit_code == 0
    assert fake_service.calls[0]["analysis_date"] == date(2026, 5, 20)
    assert fake_service.calls[0]["log_window"] == {
        "since": "2026-05-20T00:00:00Z",
        "until": "2026-05-21T00:00:00Z",
        "since_datetime": datetime(2026, 5, 20, tzinfo=UTC),
        "until_datetime": datetime(2026, 5, 21, tzinfo=UTC),
    }
    assert "analysis_date=2026-05-20" in result.output


def test_check_mcp_command_calls_mcp_service_status(
    mocker: MockerFixture,
) -> None:
    class FakeLogAnalysisService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def check_mcp_status(self) -> McpServiceStatus:
            self.calls.append("check_mcp_status")
            return McpServiceStatus(
                name="mcp-log-server",
                status="ok",
                environment="dev",
                client_type="workflow_agent",
            )

    fake_service = FakeLogAnalysisService()
    mocker.patch.object(
        main.LogAnalysisService,
        "create_default",
        return_value=fake_service,
    )

    result = runner.invoke(main.app, ["check-mcp"])

    assert result.exit_code == 0
    assert fake_service.calls == ["check_mcp_status"]
    assert "MCP service is reachable" in result.output
    assert "name=mcp-log-server" in result.output
    assert "status=ok" in result.output


def test_sitemap_analysis_command_calls_sitemap_service(
    mocker: MockerFixture,
) -> None:
    class FakeSitemapAnalysisService:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run_sitemap_analysis(
            self,
            *,
            analysis_date: date,
            force: bool,
            send_email: bool,
        ) -> SitemapAnalysisWorkflowResult:
            self.calls.append(
                {
                    "analysis_date": analysis_date,
                    "force": force,
                    "send_email": send_email,
                }
            )
            return SitemapAnalysisWorkflowResult(analysis=_sitemap_analysis_out(analysis_date))

    fake_service = FakeSitemapAnalysisService()
    mocker.patch.object(
        main.SitemapAnalysisService,
        "create_default",
        return_value=fake_service,
    )

    result = runner.invoke(main.app, ["sitemap-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code == 0
    assert "Prepared sitemap analysis record" in result.output
    assert fake_service.calls[0] == {
        "analysis_date": date(2026, 5, 19),
        "force": False,
        "send_email": True,
    }


def test_typer_commands_wrap_async_callbacks() -> None:
    assert not inspect.iscoroutinefunction(main.log_analysis)
    log_analysis = cast(Any, main.log_analysis)
    sitemap_analysis = cast(Any, main.sitemap_analysis)
    check_mcp = cast(Any, main.check_mcp)
    assert inspect.iscoroutinefunction(log_analysis.__wrapped__)
    assert not inspect.iscoroutinefunction(main.sitemap_analysis)
    assert inspect.iscoroutinefunction(sitemap_analysis.__wrapped__)
    assert not inspect.iscoroutinefunction(main.check_mcp)
    assert inspect.iscoroutinefunction(check_mcp.__wrapped__)


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
        raise IntegrityError("duplicate key value violates unique constraint")

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "Database integrity error" in output
    assert "duplicate key value violates unique constraint" in output
    assert "Database connection failed" not in output
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 1
    assert "Traceback" not in output


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
                "Check LOG_ANALYSIS_MCP_URL and whether the MCP server is running. "
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
    assert "Check LOG_ANALYSIS_MCP_URL" in output
    assert "server is running" in output
    assert "Docker Compose" in output
    assert "means the monitoring container" in output
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
    assert "Check LOG_ANALYSIS_MCP_URL" not in output
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
                "MCP collect_logs error: Unknown project 'landingpage'. "
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
    assert "Unknown project 'landingpage'" in output
    assert "No persisted manifest" in output
    assert "was found for that project" in output
    assert "Call list_projects" in output
    assert "Check LOG_ANALYSIS_MCP_URL" not in output
    assert "MCP_WORKFLOW_JWT" not in output
    assert "MCP server is running" not in output


def test_db_decorator_formats_llm_provider_configuration_errors(
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
        raise ProviderConfigurationError("OpenAI API key is required when no client is injected")

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "LLM provider configuration failed" in output
    assert "OpenAI API" in output
    assert "key is required" in output
    assert "OPENAI_API_KEY" in output
    assert "OPEN_API_KEY" in output
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
            "Private monitoring context file is required but was not found: "
            "/app/private/vps_monitoring_context.md",
            context_path="/app/private/vps_monitoring_context.md",
        )

    result = runner.invoke(app)
    output = unstyle(result.output)

    assert result.exit_code == 1
    assert "Private monitoring context is not configured" in output
    assert "/app/private/vps_monitoring_context.md" in output
    assert "MONITORING_PRIVATE_CONTEXT_PATH" in output
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

    mocker.patch("db.cli.subprocess.run", new=fake_run)
    mocker.patch("db.cli.MIGRATIONS_DIR", migrations_dir)

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

    mocker.patch("db.cli.subprocess.run", new=fake_run)
    mocker.patch("db.cli.MIGRATIONS_DIR", migrations_dir)

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

    mocker.patch("db.cli.subprocess.run", new=fake_run)
    mocker.patch("db.cli.MIGRATIONS_DIR", migrations_dir)

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
    run_migrations = mocker.patch("db.cli._run_makemigrations", return_value=7)
    mocker.patch("db.cli.sys.argv", ["makemigrations", "add_models"])

    try:
        makemigrations()
    except SystemExit as error:
        assert error.code == 7
    else:
        raise AssertionError("makemigrations should exit")

    run_migrations.assert_called_once_with(["add_models"])


def test_migrate_script_runs_aerich_upgrade(mocker: MockerFixture) -> None:
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

    mocker.patch("db.cli.subprocess.run", new=fake_run)
    mocker.patch("db.cli.sys.argv", ["migrate", "--fake"])

    try:
        migrate()
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError("migrate should exit")

    assert calls[0][0] == ["aerich", "upgrade", "--fake"]
    assert calls[0][1] is False
    assert calls[0][2] is False
