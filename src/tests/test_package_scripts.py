import tomllib
from pathlib import Path
from unittest.mock import patch

import scripts


def test_console_scripts_are_standalone_commands() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    scripts = pyproject["project"]["scripts"]

    assert "monitoring" not in scripts
    assert scripts["log_analysis"] == "scripts:log_analysis_entry"
    assert scripts["sitemap-analysis"] == "scripts:sitemap_analysis_entry"
    assert scripts["check-mcp"] == "scripts:check_mcp_entry"
    assert scripts["makemigrations"] == "db.cli:makemigrations"
    assert scripts["migrate"] == "db.cli:migrate"


def test_console_script_entrypoints_configure_logging() -> None:
    with (
        patch("scripts.configure_logging") as configure_logging,
        patch("scripts.typer.run") as typer_run,
    ):
        scripts.log_analysis_entry()

    configure_logging.assert_called_once_with(scripts.settings)
    typer_run.assert_called_once_with(scripts.log_analysis)
