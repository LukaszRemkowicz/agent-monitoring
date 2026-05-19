import inspect
import subprocess
from unittest.mock import patch

from typer.testing import CliRunner

import main
from db import cli as db_cli
from db.cli import makemigrations, migrate
from decorators import as_async, db

runner = CliRunner()


def test_cli_help_lists_phase_zero_commands():
    result = runner.invoke(main.app, ["--help"])

    assert result.exit_code == 0
    assert "log-analysis" in result.output
    assert "sitemap-analysis" in result.output
    assert "check-mcp" in result.output


def test_log_analysis_command_is_skeleton_only():
    result = runner.invoke(main.app, ["log-analysis", "--analysis-date", "2026-05-19"])

    assert result.exit_code == 0
    assert "not implemented beyond Phase 0" in result.output


def test_typer_commands_wrap_async_callbacks():
    assert not inspect.iscoroutinefunction(main.log_analysis)
    assert inspect.iscoroutinefunction(main.log_analysis.__wrapped__)
    assert not inspect.iscoroutinefunction(main.sitemap_analysis)
    assert inspect.iscoroutinefunction(main.sitemap_analysis.__wrapped__)
    assert not inspect.iscoroutinefunction(main.check_mcp)
    assert inspect.iscoroutinefunction(main.check_mcp.__wrapped__)


def test_as_async_runs_coroutine_function():
    calls: list[str] = []

    @as_async()
    async def command(name: str) -> str:
        calls.append(name)
        return name.upper()

    assert command("phase-0") == "PHASE-0"
    assert calls == ["phase-0"]


def test_db_decorator_runs_coroutine_inside_database_lifespan(monkeypatch):
    calls: list[str] = []

    class FakeDatabaseLifespan:
        async def __aenter__(self):
            calls.append("enter")

        async def __aexit__(self, exc_type, exc, traceback):
            calls.append("exit")

    def fake_database_lifespan():
        return FakeDatabaseLifespan()

    monkeypatch.setattr("decorators.database_lifespan", fake_database_lifespan)

    @as_async()
    @db
    async def command(name: str) -> str:
        calls.append(name)
        return name.upper()

    assert command("phase-0") == "PHASE-0"
    assert calls == ["enter", "phase-0", "exit"]


def test_db_decorator_can_be_called_as_factory(monkeypatch):
    calls: list[str] = []

    class FakeDatabaseLifespan:
        async def __aenter__(self):
            calls.append("enter")

        async def __aexit__(self, exc_type, exc, traceback):
            calls.append("exit")

    def fake_database_lifespan():
        return FakeDatabaseLifespan()

    monkeypatch.setattr("decorators.database_lifespan", fake_database_lifespan)

    @as_async()
    @db()
    async def command() -> str:
        calls.append("inside")
        return "done"

    assert command() == "done"
    assert calls == ["enter", "inside", "exit"]


def test_makemigrations_runs_aerich_migrate_and_numbers_file(capsys, tmp_path):
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


def test_makemigrations_uses_next_number_for_existing_migrations(tmp_path):
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


def test_makemigrations_requires_positional_migration_name(capsys):
    result = db_cli._run_makemigrations([])

    assert result == 2
    assert "Usage: makemigrations <migration_name>" in capsys.readouterr().err


def test_makemigrations_initializes_migration_folder_when_required(tmp_path):
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


def test_makemigrations_script_exits_with_makemigrations_result():
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


def test_migrate_script_runs_aerich_upgrade():
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
