"""Tests for the developer shell script."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import pytest
from pytest_mock import MockerFixture

from cli import shell


def test_developer_shell_bootstraps_monitoring_namespace(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    async def fake_initialize_database(config: dict[str, Any]) -> None:
        calls.append(f"init:{config['connections']['default']}")

    async def fake_close_database() -> None:
        calls.append("close")

    mocker.patch("cli.shell.initialize_database", fake_initialize_database)
    mocker.patch("cli.shell.close_database", fake_close_database)

    result = shell.run_shell(start_repl=False)

    output = capsys.readouterr().out
    namespace = shell.build_shell_namespace()
    assert result == 0
    assert calls == [f"init:{shell.TORTOISE_ORM['connections']['default']}", "close"]
    assert "LogAnalysis" in namespace
    assert "LogAnalysisLLMCall" in namespace
    assert "LLMCallRepository" in namespace
    assert "Preloaded imports:" in output
    assert "from db.models import LogAnalysis, LogAnalysisLLMCall" in output
    assert "from repositories import LLMCallRepository" in output


def test_developer_shell_defaults_to_compose_host_database_port() -> None:
    env = os.environ.copy()
    env.pop("DATABASE_HOST", None)
    env.pop("DATABASE_PORT", None)
    env.pop("DATABASE_PORT_HOST", None)
    expected_database_name = env.get("DATABASE_NAME", "monitoring")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("from cli import shell; print(shell.TORTOISE_ORM['connections']['default'])"),
        ],
        capture_output=True,
        check=True,
        env=env,
        text=True,
    )

    assert f"@127.0.0.1:5438/{expected_database_name}" in result.stdout


def test_developer_shell_suppresses_ipython_cross_loop_close_error(
    mocker: MockerFixture,
) -> None:
    close_database = mocker.AsyncMock(
        side_effect=RuntimeError("got Future <Future pending> attached to a different loop")
    )
    mocker.patch("cli.shell.close_database", close_database)

    shell.close_shell_database()

    close_database.assert_awaited_once()
