"""Manual log-analysis command with fixture-backed MCP."""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import typer

from agents import MonitoringWorkflowAgent
from conf import settings
from decorators import as_async, db
from devtools.data_seed import seed_manual_fixture_initial_data
from devtools.mcp import FakerMCP
from llm import get_llm_provider
from logging_config import configure_logging
from repositories import LLMCallRepository, LogAnalysisRepository
from services.email import MonitoringEmailService
from services.log_analyse import LogAnalysisService
from services.log_history_comparison import LogAnalysisHistoryComparisonService
from utils.monitoring_context import load_private_monitoring_context

DEFAULT_SCENARIO = "sensitive_path_success"
SCENARIOS = {"sensitive_path_success", "backend_5xx"}

app = typer.Typer(
    name="manual-log-analysis-fixture",
    help="Run the production log-analysis flow with fixture-backed MCP.",
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
)


@app.command("run")
@as_async()
@db
async def run_manual_fixture(
    scenario: str = typer.Option(
        DEFAULT_SCENARIO,
        "--scenario",
        help="Fixture scenario to run.",
        case_sensitive=True,
        show_default=True,
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
    compare_history: bool = typer.Option(
        True,
        "--compare-history/--no-compare-history",
        help="Compare current grouped errors with yesterday's saved analysis before the LLM call.",
    ),
) -> None:
    """Run the real log-analysis service flow with only the MCP client mocked."""

    if scenario not in SCENARIOS:
        raise typer.BadParameter(
            "Scenario must be one of: sensitive_path_success, backend_5xx.",
            param_hint="--scenario",
        )
    parsed_analysis_date = _today_in_log_timezone()
    await seed_manual_fixture_initial_data(
        target_analysis_date=parsed_analysis_date,
        clear_target=True,
    )
    log_window = LogAnalysisService.create_log_collection_window(parsed_analysis_date)
    trace_id = uuid4().hex
    mcp_client = FakerMCP(
        scenario=scenario,
        session_id=f"manual-{scenario}-{parsed_analysis_date.isoformat()}",
        target_analysis_date=parsed_analysis_date,
    )
    log_analysis_repository = LogAnalysisRepository()
    history_comparison_service = LogAnalysisHistoryComparisonService()
    service = LogAnalysisService(
        agent=MonitoringWorkflowAgent(
            mcp_client,
            llm_provider=get_llm_provider(settings.MONITORING_LLM_STRONG_MODEL),
            private_monitoring_context=load_private_monitoring_context(
                settings.MONITORING_PRIVATE_CONTEXT_PATH
            ),
            history_comparison_service=history_comparison_service,
            history_comparison_enabled=compare_history,
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
    typer.echo(f"LLM tokens used: {result.agent_context.llm_tokens_used}")
    typer.echo(f"LLM cost USD: {result.agent_context.llm_cost_usd:.6f}")
    typer.echo(f"LLM report time: {result.agent_context.llm_report_execution_time_seconds:.2f}s")
    typer.echo(f"Execution time: {result.analysis.execution_time_seconds:.2f}s")


def _echo_list(label: str, values: list[str]) -> None:
    if not values:
        typer.echo(f"{label}: none")
        return
    typer.echo(f"{label}:")
    for value in values:
        typer.echo(f"- {value}")


def _today_in_log_timezone() -> date:
    return datetime.now(ZoneInfo(settings.LOG_TIMEZONE)).date()


def main() -> None:
    configure_logging(settings)
    app()


if __name__ == "__main__":
    main()
