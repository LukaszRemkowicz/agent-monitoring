from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

import click
import typer

from conf import settings
from decorators import as_async, db
from repositories import LogAnalysisRepository, SitemapAnalysisRepository
from schemas import LogAnalysisOut, SitemapAnalysisOut
from services.cleanup import MonitoringCleanupService

reports_app = typer.Typer(help="Inspect stored monitoring reports.")
log_reports_app = typer.Typer(help="Inspect stored log-analysis reports.")
sitemap_reports_app = typer.Typer(help="Inspect stored sitemap-analysis reports.")
cleanup_app = typer.Typer(help="Clean up stored monitoring data.")
reports_app.add_typer(log_reports_app, name="log")
reports_app.add_typer(sitemap_reports_app, name="sitemap")


@log_reports_app.command("list")
@as_async()
@db
async def reports_log_list(
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum number of recent log-analysis reports to show.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of compact text.",
    ),
) -> None:
    """List recent stored log-analysis reports without rerunning analysis."""
    reports: list[LogAnalysisOut] = await LogAnalysisRepository().recent_reports(limit=limit)
    if json_output:
        _echo_json([_log_report_list_payload(report) for report in reports])
        return
    typer.echo("Recent log-analysis reports:")
    if not reports:
        typer.echo("- none")
        return
    for report in reports:
        typer.echo(_format_log_report_row(report))


@log_reports_app.command("show")
@as_async()
@db
async def reports_log_show(
    report_date: str = typer.Option(
        ...,
        "--date",
        help="Analysis date to inspect, in YYYY-MM-DD format.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of compact text.",
    ),
) -> None:
    """Show one stored log-analysis report with MCP artifact coordinates."""
    analysis_date: date = date.fromisoformat(report_date)
    report: LogAnalysisOut | None = await LogAnalysisRepository().get_by_date(analysis_date)
    if report is None:
        raise click.ClickException(f"No log-analysis report found for {analysis_date}.")
    payload: dict[str, Any] = _log_report_detail_payload(report)
    if json_output:
        _echo_json(payload)
        return
    _echo_log_report_detail(report, payload)


@sitemap_reports_app.command("list")
@as_async()
@db
async def reports_sitemap_list(
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum number of recent sitemap-analysis reports to show.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of compact text.",
    ),
) -> None:
    """List recent stored sitemap-analysis reports without rerunning analysis."""
    reports: list[SitemapAnalysisOut] = await SitemapAnalysisRepository().recent_reports(
        limit=limit
    )
    if json_output:
        _echo_json([_sitemap_report_list_payload(report) for report in reports])
        return
    typer.echo("Recent sitemap-analysis reports:")
    if not reports:
        typer.echo("- none")
        return
    for report in reports:
        typer.echo(_format_sitemap_report_row(report))


@sitemap_reports_app.command("show")
@as_async()
@db
async def reports_sitemap_show(
    report_date: str = typer.Option(
        ...,
        "--date",
        help="Analysis date to inspect, in YYYY-MM-DD format.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of compact text.",
    ),
) -> None:
    """Show one stored sitemap-analysis report with deterministic issues."""
    analysis_date: date = date.fromisoformat(report_date)
    report: SitemapAnalysisOut | None = await SitemapAnalysisRepository().get_by_date(analysis_date)
    if report is None:
        raise click.ClickException(f"No sitemap-analysis report found for {analysis_date}.")
    payload: dict[str, Any] = _sitemap_report_detail_payload(report)
    if json_output:
        _echo_json(payload)
        return
    _echo_sitemap_report_detail(report, payload)


@reports_app.command("attention")
@as_async()
@db
async def reports_attention(
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum number of failed and unsent report rows per category.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of compact text.",
    ),
) -> None:
    """List failed runs and reports whose notification email is still unsent."""
    log_repository = LogAnalysisRepository()
    sitemap_repository = SitemapAnalysisRepository()
    failed_log_reports: list[LogAnalysisOut] = await log_repository.failed_reports(limit=limit)
    unsent_log_reports: list[LogAnalysisOut] = await log_repository.unsent_emails(limit=limit)
    failed_sitemap_reports: list[SitemapAnalysisOut] = await sitemap_repository.failed_reports(
        limit=limit
    )
    unsent_sitemap_reports: list[SitemapAnalysisOut] = await sitemap_repository.unsent_emails(
        limit=limit
    )
    payload: dict[str, Any] = {
        "failed_log_reports": [_log_report_list_payload(report) for report in failed_log_reports],
        "unsent_log_reports": [_log_report_list_payload(report) for report in unsent_log_reports],
        "failed_sitemap_reports": [
            _sitemap_report_list_payload(report) for report in failed_sitemap_reports
        ],
        "unsent_sitemap_reports": [
            _sitemap_report_list_payload(report) for report in unsent_sitemap_reports
        ],
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo("Reports needing attention:")
    _echo_report_rows("Failed log reports", failed_log_reports, _format_log_report_row)
    _echo_report_rows("Unsent log emails", unsent_log_reports, _format_log_report_row)
    _echo_report_rows(
        "Failed sitemap reports",
        failed_sitemap_reports,
        _format_sitemap_report_row,
    )
    _echo_report_rows(
        "Unsent sitemap emails",
        unsent_sitemap_reports,
        _format_sitemap_report_row,
    )


@cleanup_app.command("reports")
@as_async()
@db
async def cleanup_reports(
    retention_days: int | None = typer.Option(
        None,
        "--retention-days",
        min=1,
        help=(
            "Shared fallback retention in days for log and sitemap reports. "
            "Defaults to RETENTION_DAYS when category-specific options are omitted."
        ),
    ),
    log_retention_days: int | None = typer.Option(
        None,
        "--log-retention-days",
        min=1,
        help=(
            "Delete log-analysis report rows older than this many days, except "
            "protected recent successful log-analysis history."
        ),
    ),
    sitemap_retention_days: int | None = typer.Option(
        None,
        "--sitemap-retention-days",
        min=1,
        help="Delete sitemap-analysis report rows older than this many days.",
    ),
    protected_log_history_count: int = typer.Option(
        settings.LOG_ANALYSIS_PROTECTED_HISTORY_COUNT,
        "--protected-log-history-count",
        min=0,
        help="Number of recent successful log-analysis reports to preserve for history.",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Actually delete cleanup candidates. Without this flag, only print a dry run.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of compact text.",
    ),
) -> None:
    """Dry-run or delete old report rows while keeping recent successful log history."""

    shared_retention_days = retention_days or settings.RETENTION_DAYS
    effective_log_retention_days = (
        log_retention_days
        if log_retention_days is not None
        else getattr(settings, "LOG_ANALYSIS_RETENTION_DAYS", shared_retention_days)
    )
    effective_sitemap_retention_days = (
        sitemap_retention_days
        if sitemap_retention_days is not None
        else getattr(settings, "SITEMAP_ANALYSIS_RETENTION_DAYS", shared_retention_days)
    )
    if retention_days is not None:
        if log_retention_days is None:
            effective_log_retention_days = retention_days
        if sitemap_retention_days is None:
            effective_sitemap_retention_days = retention_days
    result = await MonitoringCleanupService().cleanup_reports(
        log_retention_days=effective_log_retention_days,
        sitemap_retention_days=effective_sitemap_retention_days,
        protected_log_history_count=protected_log_history_count,
        dry_run=not confirm,
    )
    if json_output:
        _echo_json(result)
        return
    label = "Cleanup reports dry run" if result["dry_run"] else "Deleted cleanup candidates"
    counts = result["counts"]
    result_retention_days = result["retention_days"]
    typer.echo(
        f"{label}: "
        f"log_retention_days={result_retention_days['log_analyses']} "
        f"sitemap_retention_days={result_retention_days['sitemap_analyses']} "
        f"protected_log_history={result['protected_log_history_count']} "
        f"log_analyses={counts['log_analyses']} "
        f"sitemap_analyses={counts['sitemap_analyses']} "
        f"total={result['total']}"
    )


def _echo_list(label: str, values: list[str]) -> None:
    typer.echo(f"{label}:")
    if not values:
        typer.echo("- none")
        return
    for value in values:
        typer.echo(f"- {value}")


def _echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _email_state(report: LogAnalysisOut | SitemapAnalysisOut) -> str:
    return "sent" if report.email_sent else "pending"


def _format_log_report_row(report: LogAnalysisOut) -> str:
    return (
        f"- {report.analysis_date} "
        f"severity={report.severity} "
        f"status={report.status} "
        f"email={_email_state(report)} "
        f"duration={report.execution_time_seconds:.2f}s "
        f"summary={report.summary}"
    )


def _format_sitemap_report_row(report: SitemapAnalysisOut) -> str:
    return (
        f"- {report.analysis_date} "
        f"severity={report.severity} "
        f"status={report.status} "
        f"issues={len(report.issues)} "
        f"email={_email_state(report)} "
        f"root={report.root_sitemap_url} "
        f"summary={report.summary}"
    )


def _echo_report_rows(
    label: str,
    reports: list[LogAnalysisOut] | list[SitemapAnalysisOut],
    formatter: Callable[[Any], str],
) -> None:
    typer.echo(f"{label}:")
    if not reports:
        typer.echo("- none")
        return
    for report in reports:
        typer.echo(formatter(report))


def _log_report_list_payload(report: LogAnalysisOut) -> dict[str, Any]:
    return {
        "id": report.id,
        "analysis_date": report.analysis_date.isoformat(),
        "status": report.status,
        "severity": report.severity,
        "email_sent": report.email_sent,
        "execution_time_seconds": report.execution_time_seconds,
        "summary": report.summary,
        "mcp_collect_logs_id": report.mcp_collect_logs_id,
        "error_message": report.error_message,
    }


def _sitemap_report_list_payload(report: SitemapAnalysisOut) -> dict[str, Any]:
    return {
        "id": report.id,
        "analysis_date": report.analysis_date.isoformat(),
        "status": report.status,
        "severity": report.severity,
        "issue_count": len(report.issues),
        "email_sent": report.email_sent,
        "root_sitemap_url": report.root_sitemap_url,
        "execution_time_seconds": report.execution_time_seconds,
        "summary": report.summary,
        "error_message": report.error_message,
    }


def _log_report_detail_payload(report: LogAnalysisOut) -> dict[str, Any]:
    payload = _log_report_list_payload(report)
    payload.update(
        {
            "log_window_since": (
                report.log_window_since.isoformat() if report.log_window_since else None
            ),
            "log_window_until": (
                report.log_window_until.isoformat() if report.log_window_until else None
            ),
            "log_size": report.log_size,
            "key_findings": report.key_findings,
            "recommendations": report.recommendations,
            "trend_summary": report.trend_summary,
            "evidence_fingerprints": report.evidence_fingerprints,
            "coverage_sources": _log_coverage_sources(report),
            "mcp_followup_hints": _log_mcp_followup_hints(report),
        }
    )
    return payload


def _sitemap_report_detail_payload(report: SitemapAnalysisOut) -> dict[str, Any]:
    payload = _sitemap_report_list_payload(report)
    payload.update(
        {
            "total_sitemaps": report.total_sitemaps,
            "total_urls": report.total_urls,
            "issue_summary": report.issue_summary,
            "issues": report.issues,
            "key_findings": report.key_findings,
            "recommendations": report.recommendations,
            "trend_summary": report.trend_summary,
        }
    )
    return payload


def _echo_log_report_detail(report: LogAnalysisOut, payload: dict[str, Any]) -> None:
    typer.echo(f"Log report {report.analysis_date}")
    typer.echo(f"Status: {report.status}")
    typer.echo(f"Severity: {report.severity}")
    typer.echo(f"Email: {_email_state(report)}")
    typer.echo(f"Summary: {report.summary}")
    typer.echo(f"Recommendations: {report.recommendations or 'none'}")
    typer.echo(f"Trend: {report.trend_summary or 'none'}")
    typer.echo(f"Execution time: {report.execution_time_seconds:.2f}s")
    typer.echo(f"Log window: {payload['log_window_since']} -> {payload['log_window_until']}")
    typer.echo(f"MCP artifact reference: {report.mcp_collect_logs_id or 'none'}")
    typer.echo(f"Collected log size: {payload['log_size']}")
    _echo_list("Key findings", report.key_findings)
    _echo_list("Evidence fingerprints", report.evidence_fingerprints)
    typer.echo("Coverage:")
    for source in payload["coverage_sources"]:
        typer.echo(f"- {source['project_name']} source {source['source_key']}: {source['status']}")
    if not payload["coverage_sources"]:
        typer.echo("- none")
    typer.echo("MCP follow-up hints:")
    for hint in payload["mcp_followup_hints"]:
        typer.echo(
            f"- project_name={hint['project_name']} "
            f"archive_name={hint['archive_name']} "
            f"source_keys={','.join(hint['source_keys'])}"
        )
    if not payload["mcp_followup_hints"]:
        typer.echo("- none")
    if report.error_message:
        typer.echo(f"Error: {report.error_message}")


def _echo_sitemap_report_detail(report: SitemapAnalysisOut, payload: dict[str, Any]) -> None:
    typer.echo(f"Sitemap report {report.analysis_date}")
    typer.echo(f"Status: {report.status}")
    typer.echo(f"Severity: {report.severity}")
    typer.echo(f"Email: {_email_state(report)}")
    typer.echo(f"Root sitemap: {report.root_sitemap_url}")
    typer.echo(f"Discovered: {report.total_sitemaps} sitemaps, {report.total_urls} URLs")
    typer.echo(f"Issues: {len(report.issues)}")
    typer.echo(f"Summary: {report.summary}")
    _echo_list("Key findings", report.key_findings)
    typer.echo(f"Recommendations: {report.recommendations or 'none'}")
    typer.echo("Issue summary:")
    if not report.issue_summary:
        typer.echo("- none")
    for category, count in sorted(report.issue_summary.items()):
        typer.echo(f"- {category}: {count}")
    typer.echo("Deterministic issues:")
    if not report.issues:
        typer.echo("- none")
    for issue in report.issues:
        typer.echo(
            f"- {issue.get('category', 'unknown')}: "
            f"{issue.get('url', 'unknown-url')} "
            f"{issue.get('message', '')}".rstrip()
        )
    if report.error_message:
        typer.echo(f"Error: {report.error_message}")


def _log_mcp_followup_hints(report: LogAnalysisOut) -> list[dict[str, Any]]:
    collect_logs: dict[str, Any] = _collect_logs_payload(report)
    hints: list[dict[str, Any]] = []
    for project in _artifact_projects(collect_logs):
        project_name = _string_value(project.get("project_name"))
        archive_name = _string_value(project.get("snapshot_dir")) or report.mcp_collect_logs_id
        source_keys = _string_list(project.get("resolved_source_keys"))
        if project_name and archive_name:
            hints.append(
                {
                    "project_name": project_name,
                    "archive_name": archive_name,
                    "source_keys": source_keys,
                    "tools": [
                        "group_errors",
                        "inspect_proxy_activity",
                        "build_incident_bundle",
                        "grep_log_snapshot",
                    ],
                }
            )
    return hints


def _log_coverage_sources(report: LogAnalysisOut) -> list[dict[str, str]]:
    coverage_projects = report.coverage_snapshot.get("projects")
    projects = coverage_projects if isinstance(coverage_projects, list) else []
    sources: list[dict[str, str]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_name = _string_value(project.get("project_name"))
        for source in project.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_key = _string_value(source.get("source_key"))
            status = _string_value(source.get("status"))
            if project_name and source_key:
                sources.append(
                    {
                        "project_name": project_name,
                        "source_key": source_key,
                        "status": status or "unknown",
                    }
                )
    if sources:
        return sources

    collect_logs: dict[str, Any] = _collect_logs_payload(report)
    for project in _artifact_projects(collect_logs):
        project_name = _string_value(project.get("project_name"))
        for source in project.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_key = _string_value(source.get("source_key"))
            status = _string_value(source.get("status"))
            if project_name and source_key:
                sources.append(
                    {
                        "project_name": project_name,
                        "source_key": source_key,
                        "status": status or "unknown",
                    }
                )
    return sources


def _collect_logs_payload(report: LogAnalysisOut) -> dict[str, Any]:
    collect_logs = report.mcp_artifact.get("collect_logs", report.mcp_artifact)
    return collect_logs if isinstance(collect_logs, dict) else {}


def _artifact_projects(collect_logs: dict[str, Any]) -> list[dict[str, Any]]:
    projects = collect_logs.get("projects", [])
    return [project for project in projects if isinstance(project, dict)]


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
