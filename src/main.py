from __future__ import annotations

import typer

from conf import settings
from decorators import as_async, db
from logging_config import get_logger

app = typer.Typer(
    name="monitoring",
    help="Run standalone monitoring jobs.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

logger = get_logger()


@app.command("log-analysis")
@as_async()
@db
async def log_analysis(
    analysis_date: str | None = typer.Option(
        None,
        "--analysis-date",
        help="Analysis date to process. Defaults to the job service date.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Allow rerunning an existing analysis date.",
    ),
    send_email: bool = typer.Option(
        True,
        "--email/--no-email",
        help="Send the analysis email when the future job succeeds.",
    ),
) -> None:
    """Phase 0 placeholder for the scheduled log analysis job."""
    typer.echo(
        "log-analysis is not implemented beyond Phase 0 "
        f"(analysis_date={analysis_date}, force={force}, email={send_email})."
    )


@app.command("sitemap-analysis")
@as_async()
async def sitemap_analysis(
    analysis_date: str | None = typer.Option(
        None,
        "--analysis-date",
        help="Analysis date to process. Defaults to the job service date.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Allow rerunning an existing analysis date.",
    ),
    send_email: bool = typer.Option(
        True,
        "--email/--no-email",
        help="Send the sitemap email when the future job succeeds.",
    ),
) -> None:
    """Phase 0 placeholder for the scheduled sitemap analysis job."""
    typer.echo(
        "sitemap-analysis is not implemented beyond Phase 0 "
        f"(analysis_date={analysis_date}, force={force}, email={send_email})."
    )


@app.command("check-mcp")
@as_async()
async def check_mcp() -> None:
    """Phase 0 placeholder for future MCP connectivity checks."""
    typer.echo(f"MCP check is not implemented beyond Phase 0 ({settings.LOG_ANALYSIS_MCP_URL}).")
