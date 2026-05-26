from __future__ import annotations

import typer

from conf import settings
from logging_config import configure_logging
from main import check_mcp, log_analysis, sitemap_analysis


def log_analysis_entry() -> None:
    configure_logging(settings)
    typer.run(log_analysis)


def sitemap_analysis_entry() -> None:
    configure_logging(settings)
    typer.run(sitemap_analysis)


def check_mcp_entry() -> None:
    configure_logging(settings)
    typer.run(check_mcp)
