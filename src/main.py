from __future__ import annotations

from datetime import date
from urllib.parse import urlparse
from uuid import uuid4

import typer

from agents import MonitoringWorkflowAgent
from conf import settings
from decorators import as_async, db
from llm import get_monitoring_llm_provider
from logging_config import get_logger
from mcp import McpWorkflowClient
from repositories import (
    LLMCallRepository,
    LogAnalysisRepository,
    SitemapAnalysisRepository,
)
from schemas import SitemapAnalysisOut
from services.email import MonitoringEmailService
from services.log_analyse import LogAnalysisService
from services.sitemap import (
    AnalysisRunner,
    Crawler,
    LLMSummaryBuilder,
    SitemapHTTPClient,
    build_sitemap_url,
)
from utils.monitoring_context import load_private_monitoring_context

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
        help="Send the analysis email when the job succeeds.",
    ),
) -> None:
    """Prepare the MCP workflow bundle for the scheduled log analysis job."""
    parsed_analysis_date = date.fromisoformat(analysis_date) if analysis_date else date.today()
    log_window = LogAnalysisService.create_log_collection_window(parsed_analysis_date)
    trace_id = uuid4().hex
    mcp_client = McpWorkflowClient(
        base_url=settings.LOG_ANALYSIS_MCP_URL,
        workflow_jwt=settings.MCP_WORKFLOW_JWT,
    )
    log_analysis_repository = LogAnalysisRepository()
    service = LogAnalysisService(
        agent=MonitoringWorkflowAgent(
            mcp_client,
            llm_provider=get_monitoring_llm_provider(settings),
            private_monitoring_context=load_private_monitoring_context(
                settings.MONITORING_PRIVATE_CONTEXT_PATH
            ),
        ),
        repository=log_analysis_repository,
        llm_call_repository=LLMCallRepository(trace_id=trace_id),
    )
    result = await service.run_log_analysis(
        analysis_date=parsed_analysis_date,
        log_window=log_window,
        force=force,
    )
    if send_email:
        email_service = MonitoringEmailService.create_default()
        await email_service.send_log_analysis(result.analysis)
        result = result.model_copy(
            update={
                "analysis": await log_analysis_repository.update(
                    result.analysis,
                    email_sent=True,
                )
            }
        )
    workflow = result.workflow
    collect_logs = result.collect_logs
    final_report = result.agent_context.final_report
    typer.echo(
        "Completed log-analysis report "
        f"{workflow.workflow_name} "
        f"(mandatory_skills={len(workflow.mandatory_skills)}, "
        f"optional_skills={len(workflow.optional_skills)}, "
        f"tools={len(workflow.tools)}, "
        f"collected_projects={len(collect_logs.projects)}, "
        f"severity={final_report.severity}, "
        f"analysis_date={parsed_analysis_date}, force={force}, email={send_email})."
    )
    typer.echo(f"Summary: {final_report.summary}")
    typer.echo(f"Severity rationale: {final_report.severity_rationale}")
    _echo_list("Key findings", final_report.key_findings)
    _echo_list("Evidence", final_report.evidence)
    _echo_list("Coverage gaps", final_report.coverage_gaps)
    typer.echo(f"Recommendations: {final_report.recommendations}")
    _echo_list("Watch-only items", final_report.watch_only_items)
    typer.echo(f"LLM report time: {result.agent_context.llm_report_execution_time_seconds:.2f}s")
    typer.echo(f"Execution time: {result.analysis.execution_time_seconds:.2f}s")


def _echo_list(label: str, values: list[str]) -> None:
    """Print a compact CLI list section."""

    typer.echo(f"{label}:")
    if not values:
        typer.echo("- none")
        return
    for value in values:
        typer.echo(f"- {value}")


@app.command("sitemap-analysis")
@as_async()
@db
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
        help="Send the sitemap email when the job succeeds.",
    ),
) -> None:
    """Run the deterministic sitemap analysis job."""
    parsed_analysis_date: date = (
        date.fromisoformat(analysis_date) if analysis_date else date.today()
    )
    site_domain: str = settings.SITE_DOMAIN.strip()
    if not site_domain:
        typer.echo(
            "SITE_DOMAIN is required to run sitemap analysis. "
            "Set SITE_DOMAIN=example.com or SITE_DOMAIN=https://example.com.",
            err=True,
        )
        raise typer.Exit(code=1)
    parsed_site_domain = urlparse(site_domain if "://" in site_domain else f"https://{site_domain}")
    if not parsed_site_domain.netloc or parsed_site_domain.path.rstrip("/"):
        typer.echo(
            "SITE_DOMAIN must be a domain or origin, not a sitemap URL or path. "
            "Set SITE_DOMAIN=example.com or "
            "SITE_DOMAIN=https://example.com.",
            err=True,
        )
        raise typer.Exit(code=1)

    sitemap_url: str = build_sitemap_url(site_domain)
    mcp_client = McpWorkflowClient(
        base_url=settings.LOG_ANALYSIS_MCP_URL,
        workflow_jwt=settings.MCP_WORKFLOW_JWT,
    )
    crawler: Crawler = Crawler(
        client=SitemapHTTPClient(),
        sitemap_url=sitemap_url,
        site_domain=site_domain,
    )
    sitemap_repository = SitemapAnalysisRepository()
    runner: AnalysisRunner = AnalysisRunner(
        repository=sitemap_repository,
        sitemap_url=sitemap_url,
        crawler=crawler,
        summary_builder=LLMSummaryBuilder(
            llm_provider=get_monitoring_llm_provider(settings),
            mcp_client=mcp_client,
        ),
    )
    analysis: SitemapAnalysisOut = await runner.run(
        analysis_date=parsed_analysis_date,
        force=force,
    )
    if send_email:
        email_service = MonitoringEmailService.create_default()
        await email_service.send_sitemap_analysis(analysis)
        analysis = await sitemap_repository.update(analysis, email_sent=True)
    typer.echo(
        "Completed sitemap analysis "
        f"(analysis_date={parsed_analysis_date}, "
        f"severity={analysis.severity}, "
        f"total_sitemaps={analysis.total_sitemaps}, "
        f"total_urls={analysis.total_urls}, "
        f"issues={len(analysis.issues)}, "
        f"force={force}, email={send_email})."
    )
    typer.echo(f"Summary: {analysis.summary}")
    _echo_list("Key findings", analysis.key_findings)
    typer.echo(f"Recommendations: {analysis.recommendations}")
    typer.echo(f"Execution time: {analysis.execution_time_seconds:.2f}s")


@app.command("check-mcp")
@as_async()
async def check_mcp() -> None:
    """Check that the MCP service status endpoint is reachable."""
    mcp_client = McpWorkflowClient(
        base_url=settings.LOG_ANALYSIS_MCP_URL,
        workflow_jwt=settings.MCP_WORKFLOW_JWT,
    )
    logger.info(
        "checking MCP service status",
        extra={"event": "mcp_status_check_start"},
    )
    status = await mcp_client.get_service_status()
    logger.info(
        "checked MCP service status",
        extra={
            "event": "mcp_status_check_done",
            "status": status.status,
            "environment": status.environment,
            "client_type": status.client_type,
        },
    )
    typer.echo(
        "MCP service is reachable "
        f"({settings.LOG_ANALYSIS_MCP_URL}, "
        f"name={status.name}, "
        f"status={status.status}, "
        f"environment={status.environment}, "
        f"client_type={status.client_type})."
    )
