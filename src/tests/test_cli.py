import inspect
import subprocess
from datetime import date
from pathlib import Path
from socket import gaierror
from types import TracebackType
from typing import Any, cast
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

import main
from db import cli as db_cli
from db.cli import makemigrations, migrate
from decorators import as_async, db
from schemas import LogAnalysisWorkflowResult, McpServiceStatus, WorkflowBootstrap

runner = CliRunner()


def test_cli_help_lists_phase_zero_commands() -> None:
    result = runner.invoke(main.app, ["--help"])

    assert result.exit_code == 0
    assert "log-analysis" in result.output
    assert "sitemap-analysis" in result.output
    assert "check-mcp" in result.output


def test_log_analysis_command_loads_mcp_workflow_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeLogAnalysisService:
        async def run_log_analysis(
            self,
            *,
            analysis_date: date | None,
            force: bool,
            send_email: bool,
        ) -> LogAnalysisWorkflowResult:
            calls.append(
                {
                    "analysis_date": analysis_date,
                    "force": force,
                    "send_email": send_email,
                }
            )
            return LogAnalysisWorkflowResult(
                workflow=WorkflowBootstrap(
                    workflow_name="analyze_daily_log_bundle",
                    prompt="Prompt",
                    mandatory_skills=[],
                    optional_skills=[],
                    tools=[],
                ),
            )

    fake_service = FakeLogAnalysisService()
    monkeypatch.setattr(
        main.LogAnalysisService,
        "create_default",
        staticmethod(lambda: fake_service),
    )

    result = runner.invoke(main.app, ["log-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code == 0
    assert "Loaded MCP workflow bundle analyze_daily_log_bundle" in result.output
    assert calls[0] == {
        "analysis_date": date(2026, 5, 19),
        "force": False,
        "send_email": True,
    }


def test_log_analysis_command_defaults_analysis_date_to_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeDate(date):
        @classmethod
        def today(cls) -> "FakeDate":
            return cls(2026, 5, 20)

    class FakeLogAnalysisService:
        async def run_log_analysis(
            self,
            *,
            analysis_date: date | None,
            force: bool,
            send_email: bool,
        ) -> LogAnalysisWorkflowResult:
            calls.append(
                {
                    "analysis_date": analysis_date,
                    "force": force,
                    "send_email": send_email,
                }
            )
            return LogAnalysisWorkflowResult(
                workflow=WorkflowBootstrap(
                    workflow_name="analyze_daily_log_bundle",
                    prompt="Prompt",
                    mandatory_skills=[],
                    optional_skills=[],
                    tools=[],
                ),
            )

    monkeypatch.setattr(main, "date", FakeDate)
    monkeypatch.setattr(
        main.LogAnalysisService,
        "create_default",
        staticmethod(lambda: FakeLogAnalysisService()),
    )

    result = runner.invoke(main.app, ["log-analysis"])

    assert result.exit_code == 0
    assert calls[0]["analysis_date"] == date(2026, 5, 20)
    assert "analysis_date=2026-05-20" in result.output


def test_check_mcp_command_calls_mcp_service_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeLogAnalysisService:
        async def check_mcp_status(self) -> McpServiceStatus:
            calls.append("check_mcp_status")
            return McpServiceStatus(
                name="mcp-log-server",
                status="ok",
                environment="dev",
                client_type="workflow_agent",
            )

    monkeypatch.setattr(
        main.LogAnalysisService,
        "create_default",
        staticmethod(lambda: FakeLogAnalysisService()),
    )

    result = runner.invoke(main.app, ["check-mcp"])

    assert result.exit_code == 0
    assert calls == ["check_mcp_status"]
    assert "MCP service is reachable" in result.output
    assert "name=mcp-log-server" in result.output
    assert "status=ok" in result.output


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

    assert command("phase-0") == "PHASE-0"
    assert calls == ["phase-0"]


def test_db_decorator_runs_coroutine_inside_database_lifespan(
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr("decorators.database_lifespan", fake_database_lifespan)

    @as_async()
    @db
    async def command(name: str) -> str:
        calls.append(name)
        return name.upper()

    assert command("phase-0") == "PHASE-0"
    assert calls == ["enter", "phase-0", "exit"]


def test_db_decorator_can_be_called_as_factory(
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr("decorators.database_lifespan", fake_database_lifespan)

    @as_async()
    @db()
    async def command() -> str:
        calls.append("inside")
        return "done"

    assert command() == "done"
    assert calls == ["enter", "inside", "exit"]


def test_db_decorator_formats_database_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr("decorators.database_lifespan", fake_database_lifespan)

    app = typer.Typer()

    @app.command()
    @as_async()
    @db
    async def command() -> None:
        raise gaierror("nodename nor servname provided, or not known")

    result = runner.invoke(app)

    assert result.exit_code == 1
    assert "Database connection failed" in result.output
    assert "Check DATABASE_HOST" in result.output
    assert "Traceback" not in result.output


def test_makemigrations_runs_aerich_migrate_and_numbers_file(
    capsys: pytest.CaptureFixture[str],
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

    with (
        patch("db.cli.subprocess.run", fake_run),
        patch("db.cli.MIGRATIONS_DIR", migrations_dir),
    ):
        result = db_cli._run_makemigrations(["add_models"])

    assert result == 0
    assert calls[0][0] == ["aerich", "migrate", "--offline", "--name", "add_models"]
    assert calls[0][1] is True
    assert calls[0][2] is True
    assert capsys.readouterr().out == "generated\n"
    assert not generated.exists()
    assert (migrations_dir / "001_add_models.py").read_text() == "migration"


def test_makemigrations_uses_next_number_for_existing_migrations(tmp_path: Path) -> None:
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

    with (
        patch("db.cli.subprocess.run", fake_run),
        patch("db.cli.MIGRATIONS_DIR", migrations_dir),
    ):
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


def test_makemigrations_initializes_migration_folder_when_required(tmp_path: Path) -> None:
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

    with (
        patch("db.cli.subprocess.run", fake_run),
        patch("db.cli.MIGRATIONS_DIR", migrations_dir),
    ):
        result = db_cli._run_makemigrations(["initial_schema"])

    assert result == 0
    assert calls == [
        (["aerich", "migrate", "--offline", "--name", "initial_schema"], True, True),
        (["aerich", "init-migrations"], False, False),
    ]
    assert (migrations_dir / "001_initial_schema.py").exists()


def test_makemigrations_script_exits_with_makemigrations_result() -> None:
    with (
        patch("db.cli._run_makemigrations", return_value=7) as run_migrations,
        patch("db.cli.sys.argv", ["makemigrations", "add_models"]),
    ):
        try:
            makemigrations()
        except SystemExit as error:
            assert error.code == 7
        else:
            raise AssertionError("makemigrations should exit")

    run_migrations.assert_called_once_with(["add_models"])


def test_migrate_script_runs_aerich_upgrade() -> None:
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

    with (
        patch("db.cli.subprocess.run", fake_run),
        patch("db.cli.sys.argv", ["migrate", "--fake"]),
    ):
        try:
            migrate()
        except SystemExit as error:
            assert error.code == 0
        else:
            raise AssertionError("migrate should exit")

    assert calls[0][0] == ["aerich", "upgrade", "--fake"]
    assert calls[0][1] is False
    assert calls[0][2] is False
