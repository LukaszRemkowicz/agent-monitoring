import subprocess
from pathlib import Path

from pytest_mock import MockerFixture
from typer.testing import CliRunner

from cli import shell
from cli import typer as typer_cli
from cli.utils import build_prod_compose_command, get_state_dir

runner = CliRunner()


def test_state_dir_resolver_uses_configured_state_dir(tmp_path: Path) -> None:
    configured_state_dir = tmp_path / "custom-state"

    state_dir = get_state_dir(
        "prod",
        project_dir=tmp_path / "project",
        env={"STATE_DIR": str(configured_state_dir)},
    )

    assert state_dir == configured_state_dir


def test_state_dir_resolver_falls_back_to_project_state(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch("cli.utils._can_use_preferred_state_dir", return_value=False)

    state_dir = get_state_dir("prod", project_dir=tmp_path, env={})

    assert state_dir == tmp_path / ".agent" / "state" / "prod"


def test_build_prod_compose_command_passes_tag_and_args() -> None:
    assert build_prod_compose_command(
        "v0.1.1",
        ["typer", "log-analysis", "--force", "--email"],
    ) == [
        "env",
        "TAG=v0.1.1",
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "typer",
        "log-analysis",
        "--force",
        "--email",
    ]


def test_typer_script_bridges_to_prod_compose_with_extra_args(
    mocker: MockerFixture,
) -> None:
    mocker.patch("cli.utils.is_running_in_container", return_value=False)
    mocker.patch("cli.typer.sys.argv", ["typer", "log-analysis", "--no-email"])
    run = mocker.patch(
        "cli.utils.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )

    mocker.patch.dict("os.environ", {"TAG": "v0.1.1"})
    try:
        typer_cli.main()
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError("typer should exit after bridge")

    run.assert_called_once()
    assert run.call_args.args[0] == [
        "env",
        "TAG=v0.1.1",
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "typer",
        "log-analysis",
        "--no-email",
    ]


def test_typer_script_exposes_report_and_cleanup_commands() -> None:
    result = runner.invoke(typer_cli.app, ["--help"])

    assert result.exit_code == 0
    assert "reports" in result.output
    assert "cleanup" in result.output


def test_shell_script_bridges_to_saved_prod_tag(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "prod"
    state_dir.mkdir()
    (state_dir / "current_tag").write_text("v0.1.2\n", encoding="utf-8")
    mocker.patch("cli.utils.is_running_in_container", return_value=False)
    mocker.patch("cli.shell.sys.argv", ["shell", "--help"])
    run = mocker.patch(
        "cli.utils.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )

    mocker.patch.dict("os.environ", {"TAG": "", "STATE_DIR": str(state_dir)})
    try:
        shell.main()
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError("shell should exit after bridge")

    run.assert_called_once()
    assert run.call_args.args[0] == [
        "env",
        "TAG=v0.1.2",
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "shell",
        "--help",
    ]
