from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from urllib.parse import urlparse
from uuid import uuid4

import click
import typer

from agents import MonitoringWorkflowAgent
from conf import settings
from db.models import EmailDelivery
from decorators import as_async, db
from llm import get_llm_provider
from logging_config import get_logger
from mcp import McpWorkflowClient
from reports_cli import cleanup_app, reports_app
from repositories import (
    EmailDeliveryRepository,
    LLMCallRepository,
    LogAnalysisRepository,
    SitemapAnalysisRepository,
)
from schemas import EmailDeliveryIn, SitemapAnalysisOut
from services.email import MonitoringEmailService, MonitoringFailureEmail
from services.log_analyse import LogAnalysisService
from services.log_history_comparison import LogAnalysisHistoryComparisonService
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
app.add_typer(reports_app, name="reports")
app.add_typer(cleanup_app, name="cleanup")

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
        help="Send success or failure notification email for this job.",
    ),
    compare_history: bool = typer.Option(
        True,
        "--compare-history/--no-compare-history",
        help="Compare current grouped errors with yesterday's saved analysis before the LLM call.",
    ),
) -> None:
    """Prepare the MCP workflow bundle for the scheduled log analysis job."""
    parsed_analysis_date = date.fromisoformat(analysis_date) if analysis_date else date.today()
    log_window = LogAnalysisService.create_log_collection_window(parsed_analysis_date)
    trace_id = uuid4().hex
    mcp_client = McpWorkflowClient(
        base_url=settings.MCP_URL,
        workflow_jwt=settings.MCP_WORKFLOW_JWT,
    )
    log_analysis_repository = LogAnalysisRepository()
    history_comparison_service = LogAnalysisHistoryComparisonService()
    service = LogAnalysisService(
        agent=MonitoringWorkflowAgent(
            mcp_client,
            llm_provider=get_llm_provider(settings.LLM_STRONG_MODEL),
            private_monitoring_context=load_private_monitoring_context(
                settings.PROJECT_CONTEXT_PROMPT_PATH
            ),
            history_comparison_service=history_comparison_service,
            history_comparison_enabled=compare_history,
        ),
        repository=log_analysis_repository,
        llm_call_repository=LLMCallRepository(trace_id=trace_id),
    )
    try:
        result = await service.run_log_analysis(
            analysis_date=parsed_analysis_date,
            log_window=log_window,
            force=force,
        )
    except Exception as exc:
        await _send_command_failure_email(
            command_name="log_analysis",
            analysis_date=parsed_analysis_date,
            exc=exc,
            send_email=send_email,
        )
        raise
    if send_email:
        email_service = MonitoringEmailService.create_default()
        await _send_and_record_email_delivery(
            delivery_repository=EmailDeliveryRepository(),
            send_email=lambda: email_service.send_log_analysis(result.analysis),
            report_kind=EmailDelivery.ReportKind.LOG_ANALYSIS,
            report_id=result.analysis.id,
            analysis_date=result.analysis.analysis_date,
            recipient_target=EmailDelivery.RecipientTarget.LOG,
            recipients=_email_recipients(email_service, "log_recipients"),
            subject=_email_subject(email_service, "_log_analysis_subject", result.analysis),
        )
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
    typer.echo(f"LLM tokens used: {result.agent_context.llm_tokens_used}")
    typer.echo(f"LLM cost USD: {result.agent_context.llm_cost_usd:.6f}")
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
        help="Send success or failure notification email for this job.",
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
        base_url=settings.MCP_URL,
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
            llm_provider=get_llm_provider(settings.LLM_DEFAULT_MODEL),
            mcp_client=mcp_client,
        ),
    )
    try:
        analysis: SitemapAnalysisOut = await runner.run(
            analysis_date=parsed_analysis_date,
            force=force,
        )
    except Exception as exc:
        await _send_command_failure_email(
            command_name="sitemap-analysis",
            analysis_date=parsed_analysis_date,
            exc=exc,
            send_email=send_email,
        )
        raise
    if analysis.status.upper() == "FAILED":
        failure_exc = RuntimeError(analysis.error_message or "Sitemap analysis failed.")
        await _send_command_failure_email(
            command_name="sitemap-analysis",
            analysis_date=parsed_analysis_date,
            exc=failure_exc,
            send_email=send_email,
        )
        raise click.ClickException(
            "Sitemap analysis failed. "
            f"Reason: {analysis.error_message or 'No error message was stored.'}"
        )
    if send_email:
        email_service = MonitoringEmailService.create_default()
        await _send_and_record_email_delivery(
            delivery_repository=EmailDeliveryRepository(),
            send_email=lambda: email_service.send_sitemap_analysis(analysis),
            report_kind=EmailDelivery.ReportKind.SITEMAP_ANALYSIS,
            report_id=analysis.id,
            analysis_date=analysis.analysis_date,
            recipient_target=EmailDelivery.RecipientTarget.SITEMAP,
            recipients=_email_recipients(email_service, "sitemap_recipients"),
            subject=_email_subject(email_service, "_sitemap_analysis_subject", analysis),
        )
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


async def _send_command_failure_email(
    *,
    command_name: str,
    analysis_date: date,
    exc: Exception,
    send_email: bool,
) -> None:
    if not send_email:
        return
    failure = MonitoringFailureEmail(
        command_name=command_name,
        analysis_date=analysis_date,
        error_type=type(exc).__name__,
        error_message=str(exc),
        traceback_text="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )
    try:
        email_service = MonitoringEmailService.create_default()
        await _send_and_record_email_delivery(
            delivery_repository=EmailDeliveryRepository(),
            send_email=lambda: email_service.send_monitoring_failure(failure),
            report_kind=EmailDelivery.ReportKind.MONITORING_FAILURE,
            report_id=None,
            analysis_date=analysis_date,
            recipient_target=EmailDelivery.RecipientTarget.FAILURE,
            recipients=_email_recipients(email_service, "log_recipients"),
            subject=_email_subject(email_service, "_failure_subject", failure),
            suppress_send_errors=True,
        )
    except Exception as email_exc:
        logger.error(
            "failed to send monitoring failure email",
            extra={
                "event": "monitoring_failure_email_failed",
                "command_name": command_name,
                "analysis_date": str(analysis_date),
                "error": str(email_exc),
            },
        )


async def _send_and_record_email_delivery(
    *,
    delivery_repository: EmailDeliveryRepository,
    send_email: Callable[[], Awaitable[None]],
    report_kind: str,
    report_id: int | None,
    analysis_date: date | None,
    recipient_target: str,
    recipients: list[str],
    subject: str,
    suppress_send_errors: bool = False,
) -> None:
    """Send one monitoring email and persist the delivery attempt outcome.

    Successful sends create a `succeeded` row. Failed sends create a `failed`
    row with the exception text, then re-raise unless the caller is already
    handling a command-failure notification path.
    """

    try:
        await send_email()
    except Exception as exc:
        await delivery_repository.create(
            EmailDeliveryIn(
                report_kind=report_kind,
                report_id=report_id,
                analysis_date=analysis_date,
                recipient_target=recipient_target,
                recipients=recipients,
                subject=subject,
                status=EmailDelivery.Status.FAILED,
                error_message=str(exc),
            )
        )
        if suppress_send_errors:
            return
        raise

    await delivery_repository.create(
        EmailDeliveryIn(
            report_kind=report_kind,
            report_id=report_id,
            analysis_date=analysis_date,
            recipient_target=recipient_target,
            recipients=recipients,
            subject=subject,
            status=EmailDelivery.Status.SUCCEEDED,
            sent_at=datetime.now(UTC),
        )
    )


def _email_recipients(email_service: object, attribute_name: str) -> list[str]:
    config = getattr(email_service, "config", None)
    recipients = getattr(config, attribute_name, [])
    return [str(recipient) for recipient in recipients]


def _email_subject(email_service: object, method_name: str, context: object) -> str:
    if not isinstance(email_service, MonitoringEmailService):
        return ""
    method = getattr(email_service, method_name, None)
    if not callable(method):
        return ""
    return str(method(context))


@app.command("check-mcp")
@as_async()
async def check_mcp() -> None:
    """Check that the MCP service status endpoint is reachable."""
    mcp_client = McpWorkflowClient(
        base_url=settings.MCP_URL,
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
        f"({settings.MCP_URL}, "
        f"name={status.name}, "
        f"status={status.status}, "
        f"environment={status.environment}, "
        f"client_type={status.client_type})."
    )
