"""Grouped monitoring Typer command entrypoint."""

from __future__ import annotations

import sys

import typer as typer_lib

from cli.utils import run_prod_compose_command, should_bridge_to_prod_compose
from conf import settings
from logging_config import configure_logging
from main import check_mcp, log_analysis, sitemap_analysis

app = typer_lib.Typer(
    name="typer",
    help="Run monitoring Typer commands.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.command("log-analysis")(log_analysis)
app.command("sitemap-analysis")(sitemap_analysis)
app.command("check-mcp")(check_mcp)


def main() -> None:
    if should_bridge_to_prod_compose():
        raise SystemExit(run_prod_compose_command(["typer", *sys.argv[1:]]))
    configure_logging(settings)
    app()
