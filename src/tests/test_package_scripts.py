import subprocess
from pathlib import Path

from pytest_mock import MockerFixture
from typer.testing import CliRunner

import scripts
from scripts import run as run_cli

runner = CliRunner()


def test_console_script_entrypoints_configure_logging(mocker: MockerFixture) -> None:
    configure_logging = mocker.patch("scripts.configure_logging")
    typer_run = mocker.patch("scripts.typer.run")

    scripts.log_analysis_entry()

    configure_logging.assert_called_once_with(scripts.settings)
    typer_run.assert_called_once_with(scripts.log_analysis)


def test_monitoring_entrypoint_runs_full_typer_app(mocker: MockerFixture) -> None:
    configure_logging = mocker.patch("scripts.configure_logging")
    app = mocker.patch("scripts.app")

    scripts.monitoring_entry()

    configure_logging.assert_called_once_with(scripts.settings)
    app.assert_called_once_with()


def test_monitoring_run_uses_local_compose_defaults(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        "os.environ",
        {
            "LOG_FORMAT": "json",
            "LOG_COLOR": "auto",
            "PROD_STATE_DIR": str(tmp_path),
        },
        clear=True,
    )
    run = mocker.patch(
        "scripts.run.subprocess.run",
        side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0),
            subprocess.CompletedProcess(args=[], returncode=0),
        ],
    )

    result = runner.invoke(run_cli.app, ["log-analysis", "--force", "--email"])

    assert result.exit_code == 0
    assert run.call_args_list[0].args[0] == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yaml",
        "run",
        "--rm",
        "monitoring-app",
        "migrate",
    ]
    assert run.call_args_list[1].args[0] == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yaml",
        "run",
        "--rm",
        "monitoring-app",
        "log_analysis",
        "--force",
        "--email",
    ]
    assert run.call_args_list[1].kwargs["env"]["LOG_FORMAT"] == "pretty"
    assert run.call_args_list[1].kwargs["env"]["LOG_COLOR"] == "always"
    assert "COMPOSE_PROJECT_NAME" not in run.call_args_list[1].kwargs["env"]


def test_monitoring_run_uses_deployed_prod_tag(mocker: MockerFixture, tmp_path: Path) -> None:
    state_dir = tmp_path / "prod"
    state_dir.mkdir()
    (state_dir / "current_tag").write_text("v0.1.0\n", encoding="utf-8")
    mocker.patch.dict("os.environ", {"PROD_STATE_DIR": str(state_dir)}, clear=True)
    run = mocker.patch(
        "scripts.run.subprocess.run",
        side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0),
            subprocess.CompletedProcess(args=[], returncode=0),
        ],
    )

    result = runner.invoke(run_cli.app, ["log-analysis", "--force"])

    assert result.exit_code == 0
    assert run.call_args_list[0].args[0] == [
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "migrate",
    ]
    assert run.call_args_list[1].args[0] == [
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "log_analysis",
        "--force",
    ]
    assert run.call_args_list[1].kwargs["env"]["TAG"] == "v0.1.0"
    assert "COMPOSE_PROJECT_NAME" not in run.call_args_list[1].kwargs["env"]


def test_monitoring_run_check_mcp_skips_migration(mocker: MockerFixture) -> None:
    mocker.patch.dict("os.environ", {"TAG": "v0.1.1"}, clear=True)
    run = mocker.patch(
        "scripts.run.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )

    result = runner.invoke(run_cli.app, ["check-mcp"])

    assert result.exit_code == 0
    run.assert_called_once()
    assert run.call_args.args[0] == [
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "check-mcp",
    ]
    assert run.call_args.kwargs["env"]["TAG"] == "v0.1.1"
    assert "COMPOSE_PROJECT_NAME" not in run.call_args.kwargs["env"]
