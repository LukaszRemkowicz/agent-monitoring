"""Grouped monitoring Typer command entrypoint."""

from __future__ import annotations

import sys

from cli.utils import run_prod_compose_command, should_bridge_to_prod_compose
from conf import settings
from logging_config import configure_logging
from main import app


def main() -> None:
    if should_bridge_to_prod_compose():
        raise SystemExit(run_prod_compose_command(["typer", *sys.argv[1:]]))
    configure_logging(settings)
    app()
