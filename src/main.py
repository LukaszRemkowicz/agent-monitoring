from __future__ import annotations

from datetime import date

import typer

from conf import settings
from decorators import as_async, db
from logging_config import get_logger
from services import LogAnalysisService, SitemapAnalysisService

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
        help="Analysis date to process. Defaults to today.",
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
    """Prepare the MCP workflow bundle for the scheduled log analysis job."""
    parsed_analysis_date = date.fromisoformat(analysis_date) if analysis_date else date.today()
    log_window = LogAnalysisService.create_log_collection_window(parsed_analysis_date)
    service = LogAnalysisService.create_default()
    result = await service.run_log_analysis(
        analysis_date=parsed_analysis_date,
        log_window=log_window,
        force=force,
        send_email=send_email,
    )
    workflow = result.workflow
    collect_logs = result.collect_logs
    typer.echo(
        "Prepared log-analysis prompt "
        f"{workflow.workflow_name} "
        f"(mandatory_skills={len(workflow.mandatory_skills)}, "
        f"optional_skills={len(workflow.optional_skills)}, "
        f"tools={len(workflow.tools)}, "
        f"collected_projects={len(collect_logs.projects)}, "
        f"analysis_date={parsed_analysis_date}, force={force}, email={send_email})."
    )
    typer.echo("\nPrepared LLM system prompt:")
    typer.echo(result.prepared_prompt.system_prompt)
    typer.echo("\nPrepared LLM user prompt:")
    typer.echo(result.prepared_prompt.user_prompt)


@app.command("sitemap-analysis")
@as_async()
async def sitemap_analysis(
    analysis_date: str | None = typer.Option(
        None,
        "--analysis-date",
        help="Analysis date to process. Defaults to today.",
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
    """Prepare the sitemap analysis workflow record."""
    parsed_analysis_date = date.fromisoformat(analysis_date) if analysis_date else date.today()
    service = SitemapAnalysisService.create_default()
    await service.run_sitemap_analysis(
        analysis_date=parsed_analysis_date,
        force=force,
        send_email=send_email,
    )
    typer.echo(
        "Prepared sitemap analysis record "
        f"(analysis_date={parsed_analysis_date}, force={force}, email={send_email})."
    )


@app.command("check-mcp")
@as_async()
async def check_mcp() -> None:
    """Check that the MCP service status endpoint is reachable."""
    service = LogAnalysisService.create_default()
    status = await service.check_mcp_status()
    typer.echo(
        "MCP service is reachable "
        f"({settings.LOG_ANALYSIS_MCP_URL}, "
        f"name={status.name}, "
        f"status={status.status}, "
        f"environment={status.environment}, "
        f"client_type={status.client_type})."
    )
