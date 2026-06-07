"""Seed stable initial data for the manual log-analysis fixture DB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from tortoise import Tortoise

from conf import settings
from db.models import LogAnalysis, LogAnalysisLLMCall, RunStatus
from devtools.mcp import FakerMCP
from schemas import (
    LogAnalysisFingerprintCollection,
    LogAnalysisFingerprintLogWindow,
    LogAnalysisFingerprints,
    LogAnalysisGroupedErrorHistorySummary,
    LogAnalysisGroupedErrorRunFingerprint,
    LogAnalysisGroupedErrorsResult,
    LogAnalysisReportFingerprint,
)


@dataclass(frozen=True)
class SeedManualFixtureDataResult:
    """Summary of stable initial data prepared for one manual fixture run."""

    target_analysis_date: date
    cleared_target_rows: int
    baseline_analysis_id: int
    baseline_analysis_date: date
    watch_analysis_id: int
    watch_analysis_date: date


async def seed_manual_fixture_initial_data(
    *,
    target_analysis_date: date,
    clear_target: bool = True,
) -> SeedManualFixtureDataResult:
    """Prepare stable prior history rows for a manual fixture run."""

    await Tortoise.generate_schemas(safe=True)
    if clear_target:
        deleted_target_rows = await LogAnalysis.filter(analysis_date=target_analysis_date).delete()
        await LogAnalysisLLMCall.filter(analysis_date=target_analysis_date).delete()
    else:
        deleted_target_rows = 0

    baseline_date = target_analysis_date - timedelta(days=1)
    watch_date = target_analysis_date - timedelta(days=2)

    baseline = await _upsert_log_analysis(
        analysis_date=baseline_date,
        summary=(
            "Routine scanner noise was blocked at the edge. No successful sensitive-path "
            "access or service-impacting backend errors were present."
        ),
        severity=LogAnalysis.Severity.INFO,
        key_findings=[
            "Blocked /.git/config probes returned 403.",
            "Routine WordPress scanner paths returned 404.",
            "No current /.env 200 evidence was observed in the baseline.",
        ],
        recommendations=(
            "Keep edge deny rules and access-log review in place. Treat a future 2xx on "
            "sensitive paths as new evidence, not as the same blocked scanner pattern."
        ),
        trend_summary="Stable blocked scanner traffic; no escalation required.",
        fingerprints=_baseline_fingerprints(
            analysis_date=baseline_date,
            severity=LogAnalysis.Severity.INFO,
        ),
        email_sent=True,
        gpt_tokens_used=820,
        gpt_cost_usd=0.004,
    )
    watch = await _upsert_log_analysis(
        analysis_date=watch_date,
        summary=(
            "Minor transient worker retries appeared, but the public edge and backend "
            "remained healthy."
        ),
        severity=LogAnalysis.Severity.WARNING,
        key_findings=[
            "A small number of image translation retries were logged.",
            "No persistent backend 5xx pattern was present.",
        ],
        recommendations="Watch worker retry volume, but no incident action is required.",
        trend_summary="Low-volume operational noise only.",
        fingerprints=_watch_only_fingerprints(
            analysis_date=watch_date,
            severity=LogAnalysis.Severity.WARNING,
        ),
        email_sent=True,
        gpt_tokens_used=640,
        gpt_cost_usd=0.003,
    )
    return SeedManualFixtureDataResult(
        target_analysis_date=target_analysis_date,
        cleared_target_rows=deleted_target_rows,
        baseline_analysis_id=baseline.id,
        baseline_analysis_date=baseline.analysis_date,
        watch_analysis_id=watch.id,
        watch_analysis_date=watch.analysis_date,
    )


async def _upsert_log_analysis(
    *,
    analysis_date: date,
    summary: str,
    severity: str,
    key_findings: list[str],
    recommendations: str,
    trend_summary: str,
    fingerprints: LogAnalysisFingerprints,
    email_sent: bool,
    gpt_tokens_used: int,
    gpt_cost_usd: float,
) -> LogAnalysis:
    log_window_since, log_window_until = _log_window_bounds(analysis_date)
    data: dict[str, Any] = {
        "mcp_artifact": {
            "seed": "manual-fixture",
            "analysis_date": analysis_date.isoformat(),
            "scenario": "stable-history-baseline",
        },
        "status": RunStatus.SUCCEEDED.value,
        "started_at": log_window_until + timedelta(minutes=1),
        "finished_at": log_window_until + timedelta(minutes=2),
        "failure_stage": None,
        "log_window_since": log_window_since,
        "log_window_until": log_window_until,
        "mcp_collect_logs_id": f"manual-fixture-seed-{analysis_date.isoformat()}",
        "summary": summary,
        "severity": severity,
        "key_findings": key_findings,
        "recommendations": recommendations,
        "trend_summary": trend_summary,
        "fingerprints": fingerprints.model_dump(mode="json"),
        "evidence_fingerprints": [
            group.fingerprint
            for run in fingerprints.grouped_error_runs
            for group in run.result.groups
        ],
        "known_patterns": [
            {
                "name": "blocked-sensitive-path-probes",
                "status": "expected_noise_when_blocked",
                "fingerprint": "landingpage:edge:http_403:/.git/config",
            },
            {
                "name": "wordpress-scanner-noise",
                "status": "expected_noise",
                "fingerprint": "landingpage:edge:http_404:scanner-wordpress",
            },
        ],
        "coverage_snapshot": _coverage_snapshot(analysis_date),
        "fingerprint_version": fingerprints.version,
        "execution_time_seconds": 2.4,
        "gpt_tokens_used": gpt_tokens_used,
        "gpt_cost_usd": gpt_cost_usd,
        "email_sent": email_sent,
        "error_message": "",
    }
    existing = await LogAnalysis.get_or_none(analysis_date=analysis_date)
    if existing is None:
        return await LogAnalysis.create(analysis_date=analysis_date, **data)
    for field_name, value in data.items():
        setattr(existing, field_name, value)
    await existing.save(update_fields=list(data))
    return existing


def _baseline_fingerprints(*, analysis_date: date, severity: str) -> LogAnalysisFingerprints:
    grouped_errors = FakerMCP.load_fixture_payload(
        "sensitive_path_success",
        "group_errors",
        target_analysis_date=analysis_date,
    )
    stable_groups = [
        group
        for group in grouped_errors["groups"]
        if group["fingerprint"]
        in {
            "landingpage:edge:http_403:/.git/config",
            "landingpage:edge:http_404:scanner-wordpress",
        }
    ]
    grouped_errors["groups"] = stable_groups
    grouped_errors["grouped_error_count"] = len(stable_groups)
    grouped_errors["matching_line_count"] = sum(int(group["count"]) for group in stable_groups)
    grouped_errors["summary"] = "Stable blocked scanner traffic only."
    return _fingerprints_from_grouped_errors(
        analysis_date=analysis_date,
        grouped_errors=grouped_errors,
        severity=severity,
    )


def _watch_only_fingerprints(*, analysis_date: date, severity: str) -> LogAnalysisFingerprints:
    grouped_errors = FakerMCP.load_fixture_payload(
        "sensitive_path_success",
        "group_errors",
        target_analysis_date=analysis_date,
    )
    stable_groups = [
        group
        for group in grouped_errors["groups"]
        if group["fingerprint"] == "landingpage:backend:error:celery:image-translation-timeout"
    ]
    grouped_errors["groups"] = stable_groups
    grouped_errors["grouped_error_count"] = len(stable_groups)
    grouped_errors["matching_line_count"] = sum(int(group["count"]) for group in stable_groups)
    grouped_errors["summary"] = "Watch-only worker retry pattern."
    return _fingerprints_from_grouped_errors(
        analysis_date=analysis_date,
        grouped_errors=grouped_errors,
        severity=severity,
    )


def _fingerprints_from_grouped_errors(
    *,
    analysis_date: date,
    grouped_errors: dict[str, object],
    severity: str,
) -> LogAnalysisFingerprints:
    grouped_error_result = LogAnalysisGroupedErrorsResult.from_mcp_payload(grouped_errors)
    since, until = _mcp_window_strings(analysis_date)
    return LogAnalysisFingerprints(
        version="log-analysis-fingerprints-v1",
        log_window=LogAnalysisFingerprintLogWindow(since=since, until=until),
        collection=LogAnalysisFingerprintCollection(
            workspace="workflow",
            requested_project_names=["landingpage", "vps-security"],
            project_count=2,
        ),
        coverage_totals={
            "projects": 2,
            "sources": 8,
            "collected_sources": 8,
            "unavailable_sources": 0,
            "zero_line_sources": 0,
        },
        grouped_error_runs=[
            LogAnalysisGroupedErrorRunFingerprint(
                arguments={"project_name": "landingpage"},
                result=grouped_error_result,
            )
        ],
        grouped_error_history_summary=LogAnalysisGroupedErrorHistorySummary(
            signal_count=len(grouped_error_result.groups),
            run_count=1,
            detail=grouped_error_result.summary,
        ),
        report=LogAnalysisReportFingerprint(
            severity=severity,
            key_finding_count=len(grouped_error_result.groups),
            evidence_count=len(grouped_error_result.groups),
            coverage_gap_count=0,
            watch_only_count=1,
        ),
    )


def _log_window_bounds(analysis_date: date) -> tuple[datetime, datetime]:
    local_timezone = ZoneInfo(settings.LOG_TIMEZONE)
    local_window_since = datetime.combine(analysis_date, time.min, tzinfo=local_timezone)
    local_window_until = local_window_since + timedelta(days=1)
    return local_window_since.astimezone(UTC), local_window_until.astimezone(UTC)


def _mcp_window_strings(analysis_date: date) -> tuple[str, str]:
    since, until = _log_window_bounds(analysis_date)
    return (
        since.isoformat().replace("+00:00", "Z"),
        until.isoformat().replace("+00:00", "Z"),
    )


def _coverage_snapshot(analysis_date: date) -> dict[str, object]:
    return {
        "projects": [
            {
                "project_name": "landingpage",
                "snapshot_dir": f"workflow/landingpage/{analysis_date.isoformat()}",
                "warnings": [],
                "sources": [
                    _coverage_source("nginx", "file", 920, 65536),
                    _coverage_source("traefik", "file", 760, 49152),
                    _coverage_source("backend", "docker", 180, 12288),
                    _coverage_source("frontend", "docker", 90, 8192),
                    _coverage_source("celery_worker", "docker", 75, 6144),
                    _coverage_source("celery_beat", "docker", 30, 2048),
                ],
            },
            {
                "project_name": "vps-security",
                "snapshot_dir": f"workflow/vps-security/{analysis_date.isoformat()}",
                "warnings": [],
                "sources": [
                    _coverage_source("auth", "file", 220, 16384),
                    _coverage_source("fail2ban", "file", 64, 4096),
                ],
            },
        ],
        "totals": {
            "projects": 2,
            "sources": 8,
            "collected_sources": 8,
            "unavailable_sources": 0,
            "zero_line_sources": 0,
        },
    }


def _coverage_source(
    source_key: str,
    source_type: str,
    line_count: int,
    byte_count: int,
) -> dict[str, object]:
    return {
        "source_key": source_key,
        "source_type": source_type,
        "status": "collected",
        "line_count": line_count,
        "byte_count": byte_count,
        "zero_lines": False,
        "has_output_file": True,
        "error": None,
    }
