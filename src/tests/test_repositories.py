from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from db.models import EmailDelivery, LogAnalysis, LogAnalysisLLMCall, SitemapAnalysis
from repositories import (
    EmailDeliveryRepository,
    LLMCallRepository,
    LogAnalysisRepository,
    SitemapAnalysisRepository,
)
from schemas import (
    EmailDeliveryIn,
    EmailDeliveryOut,
    LogAnalysisFingerprints,
    LogAnalysisIn,
    LogAnalysisLLMCallIn,
    LogAnalysisOut,
    SitemapAnalysisIn,
    SitemapAnalysisOut,
)
from tests.factories import LogAnalysisFactory, SitemapAnalysisFactory
from utils.log_artifacts import is_compressed_json_mapping


@pytest.mark.asyncio
async def test_email_delivery_repository_records_attempts_and_filters_recent_failures() -> None:
    repository = EmailDeliveryRepository()
    sent_at = datetime(2026, 5, 19, 8, 30, tzinfo=UTC)

    succeeded = await repository.create(
        EmailDeliveryIn(
            report_kind=EmailDelivery.ReportKind.LOG_ANALYSIS,
            report_id=42,
            analysis_date=date(2026, 5, 19),
            recipient_target=EmailDelivery.RecipientTarget.LOG,
            recipients=["ops@example.com"],
            subject="[PROD][INFO] Daily Log Analysis - 2026-05-19",
            status=EmailDelivery.Status.SUCCEEDED,
            sent_at=sent_at,
            provider_message_id="smtp-message-1",
        )
    )
    failed = await repository.create(
        EmailDeliveryIn(
            report_kind=EmailDelivery.ReportKind.SITEMAP_ANALYSIS,
            report_id=7,
            analysis_date=date(2026, 5, 20),
            recipient_target=EmailDelivery.RecipientTarget.SITEMAP,
            recipients=["seo@example.com"],
            subject="[PROD][CRITICAL] Sitemap Analysis - 2026-05-20",
            status=EmailDelivery.Status.FAILED,
            error_message="SMTP timeout",
        )
    )

    recent = await repository.recent(limit=5)
    failures = await repository.failed(limit=5)

    assert isinstance(succeeded, EmailDeliveryOut)
    assert succeeded.id > 0
    assert succeeded.sent_at == sent_at
    assert succeeded.provider_message_id == "smtp-message-1"
    assert failed.error_message == "SMTP timeout"
    assert [delivery.id for delivery in recent] == [failed.id, succeeded.id]
    assert [delivery.id for delivery in failures] == [failed.id]


@pytest.mark.asyncio
async def test_log_analysis_repository_filters_with_model_manager() -> None:
    repository = LogAnalysisRepository()
    analysis = await LogAnalysisFactory.create(severity=LogAnalysis.Severity.WARNING.value)

    assert await repository.filter(severity=LogAnalysis.Severity.WARNING.value).first() == analysis


@pytest.mark.asyncio
async def test_log_analysis_repository_checks_existence_with_filters() -> None:
    repository = LogAnalysisRepository()
    await LogAnalysisFactory.create(severity=LogAnalysis.Severity.CRITICAL.value)

    assert await repository.exists(severity=LogAnalysis.Severity.CRITICAL.value) is True
    assert await repository.exists(severity=LogAnalysis.Severity.INFO.value) is False


@pytest.mark.asyncio
async def test_log_analysis_repository_creates_with_contract_and_returns_output() -> None:
    repository = LogAnalysisRepository()

    analysis = await repository.create(
        LogAnalysisIn(
            analysis_date=date(2026, 5, 19),
            status="running",
            summary="Workflow preparation started.",
        )
    )

    assert isinstance(analysis, LogAnalysisOut)
    assert analysis.id > 0
    assert analysis.analysis_date == date(2026, 5, 19)
    assert analysis.status == "running"


@pytest.mark.asyncio
async def test_log_analysis_repository_updates_contract_with_kwargs() -> None:
    repository = LogAnalysisRepository()
    analysis = await repository.create(
        LogAnalysisIn(
            analysis_date=date(2026, 5, 19),
            status="running",
            summary="Workflow preparation started.",
        )
    )

    updated = await repository.update(
        analysis,
        status="succeeded",
        summary="Workflow bundle loaded.",
    )

    assert updated.id == analysis.id
    assert updated.status == "succeeded"
    assert updated.summary == "Workflow bundle loaded."


@pytest.mark.asyncio
async def test_log_analysis_repository_compresses_large_json_fields_at_rest() -> None:
    repository = LogAnalysisRepository()
    mcp_artifact = {
        "collect_logs": {
            "projects": [
                {
                    "project_name": "landingpage",
                    "sources": [
                        {
                            "source_key": "backend",
                            "byte_count": 4096,
                            "payload": "same-line\n" * 200,
                        }
                    ],
                }
            ]
        }
    }
    fingerprints = LogAnalysisFingerprints.model_validate(
        {
            "version": "log-analysis-fingerprint-v1",
            "coverage_totals": {"backend": 1},
            "tool_results": [
                {
                    "tool_name": "group_errors",
                    "arguments_hash": "args-hash",
                    "result_hash": "abc",
                }
            ],
        }
    )
    coverage_snapshot = {
        "projects": [{"project_name": "landingpage", "sources": [{"source_key": "backend"}]}],
        "totals": {"sources": 1},
    }

    analysis = await repository.create(
        LogAnalysisIn(
            analysis_date=date(2026, 5, 19),
            status="succeeded",
            summary="Workflow completed.",
            mcp_artifact=mcp_artifact,
            fingerprints=fingerprints,
            coverage_snapshot=coverage_snapshot,
        )
    )
    raw_analysis = await LogAnalysis.get(id=analysis.id)

    assert is_compressed_json_mapping(raw_analysis.mcp_artifact)
    assert is_compressed_json_mapping(raw_analysis.fingerprints)
    assert is_compressed_json_mapping(raw_analysis.coverage_snapshot)
    assert analysis.mcp_artifact == mcp_artifact
    assert analysis.fingerprints.coverage_totals == {"backend": 1}
    assert analysis.coverage_snapshot == coverage_snapshot


@pytest.mark.asyncio
async def test_log_analysis_repository_keeps_compressed_json_after_update() -> None:
    repository = LogAnalysisRepository()
    analysis = await repository.create(
        LogAnalysisIn(
            analysis_date=date(2026, 5, 19),
            status="running",
            summary="Workflow preparation started.",
            mcp_artifact={"collect_logs": {"projects": []}},
            coverage_snapshot={"totals": {"sources": 0}},
        )
    )

    updated = await repository.update(analysis, email_sent=True)
    raw_analysis = await LogAnalysis.get(id=analysis.id)

    assert updated.email_sent is True
    assert is_compressed_json_mapping(raw_analysis.mcp_artifact)
    assert is_compressed_json_mapping(raw_analysis.fingerprints)
    assert is_compressed_json_mapping(raw_analysis.coverage_snapshot)


@pytest.mark.asyncio
async def test_log_analysis_repository_get_latest_before_date_returns_previous_success() -> None:
    repository = LogAnalysisRepository()
    await LogAnalysisFactory.create(
        analysis_date=date(2026, 5, 17),
        status="succeeded",
        summary="Older recurring scanner noise.",
        fingerprint_version="log-history-v1",
    )
    latest = await LogAnalysisFactory.create(
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Latest recurring scanner noise.",
        fingerprint_version="log-history-v1",
        fingerprints={"coverage_totals": {"404": 12}},
        evidence_fingerprints=["scanner-family:generic-env-probe"],
        known_patterns=[{"family": "scanner", "status": "watch_only"}],
        coverage_snapshot={"demo-shop": {"backend": "collected"}},
    )
    await LogAnalysisFactory.create(
        analysis_date=date(2026, 5, 19),
        status="succeeded",
        summary="Current run must be excluded.",
        fingerprint_version="log-history-v1",
    )
    await LogAnalysisFactory.create(
        analysis_date=date(2026, 5, 16),
        status="failed",
        summary="Failed run must not be used.",
        fingerprint_version="log-history-v1",
    )
    await LogAnalysisFactory.create(
        analysis_date=date(2026, 5, 15),
        status="succeeded",
        summary="No structured history must not be used.",
    )

    baseline = await repository.get_latest_before_date(date(2026, 5, 19))

    assert baseline is not None
    assert baseline.id == latest.id
    assert baseline.summary == "Latest recurring scanner noise."
    assert baseline.fingerprints.coverage_totals == {"404": 12}
    assert baseline.evidence_fingerprints == ["scanner-family:generic-env-probe"]
    assert baseline.known_patterns == [{"family": "scanner", "status": "watch_only"}]
    assert baseline.coverage_snapshot == {"demo-shop": {"backend": "collected"}}


@pytest.mark.asyncio
async def test_log_analysis_repository_returns_operational_reads() -> None:
    repository = LogAnalysisRepository()
    today = date.today()
    old_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=30),
        status="succeeded",
        summary="Old report for retention cleanup.",
        email_sent=True,
    )
    successful_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=2),
        status="succeeded",
        summary="Recent successful report.",
        email_sent=True,
    )
    critical_unsent_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=1),
        status="failed",
        severity=LogAnalysis.Severity.CRITICAL.value,
        summary="Failed critical report.",
        email_sent=False,
    )
    older_critical_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=3),
        status="failed",
        severity=LogAnalysis.Severity.CRITICAL.value,
        summary="Older critical report.",
        email_sent=True,
    )
    warning_unsent_report = await LogAnalysisFactory.create(
        analysis_date=today,
        status="succeeded",
        severity=LogAnalysis.Severity.WARNING.value,
        summary="Warning report awaiting email.",
        email_sent=False,
    )

    recent_history = await repository.recent_history(limit=2)
    critical_reports = await repository.critical_reports(limit=1)
    unsent_emails = await repository.unsent_emails(limit=1)
    retention_candidates = await repository.retention_candidates(older_than_days=5, limit=1)

    assert [report.id for report in recent_history] == [
        warning_unsent_report.id,
        successful_report.id,
    ]
    assert [report.id for report in critical_reports] == [critical_unsent_report.id]
    assert older_critical_report.id != critical_unsent_report.id
    assert [report.id for report in unsent_emails] == [warning_unsent_report.id]
    assert [report.id for report in retention_candidates] == [old_report.id]


@pytest.mark.asyncio
async def test_log_analysis_repository_retention_excludes_recent_successful_history() -> None:
    repository = LogAnalysisRepository()
    today = date.today()
    very_old_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=60),
        status="succeeded",
        summary="Very old report can be deleted.",
        email_sent=True,
    )
    protected_history_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=40),
        status="succeeded",
        summary="Old but still part of recent successful history.",
        email_sent=True,
    )
    await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=2),
        status="succeeded",
        summary="Recent successful report.",
        email_sent=True,
    )
    await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=1),
        status="succeeded",
        summary="Newest successful report.",
        email_sent=True,
    )

    candidate_ids = await repository.retention_candidate_ids(
        older_than_days=30,
        keep_recent_successful=3,
    )
    deleted_count = await repository.delete_retention_candidates(
        older_than_days=30,
        keep_recent_successful=3,
    )

    assert candidate_ids == [very_old_report.id]
    assert deleted_count == 1
    assert await LogAnalysis.filter(id=very_old_report.id).exists() is False
    assert await LogAnalysis.filter(id=protected_history_report.id).exists() is True


@pytest.mark.asyncio
async def test_log_analysis_repository_returns_recent_reports_and_failed_runs() -> None:
    repository = LogAnalysisRepository()
    older_report = await LogAnalysisFactory.create(
        analysis_date=date(2026, 5, 17),
        status="succeeded",
        summary="Older report.",
    )
    failed_report = await LogAnalysisFactory.create(
        analysis_date=date(2026, 5, 18),
        status="failed",
        severity=LogAnalysis.Severity.CRITICAL.value,
        summary="Failed report.",
    )
    newest_report = await LogAnalysisFactory.create(
        analysis_date=date(2026, 5, 19),
        status="succeeded",
        summary="Newest report.",
    )

    recent_reports = await repository.recent_reports(limit=2)
    failed_reports = await repository.failed_reports(limit=5)

    assert [report.id for report in recent_reports] == [newest_report.id, failed_report.id]
    assert older_report.id not in [report.id for report in recent_reports]
    assert [report.id for report in failed_reports] == [failed_report.id]


@pytest.mark.asyncio
async def test_sitemap_analysis_repository_filters_with_model_manager() -> None:
    repository = SitemapAnalysisRepository()
    analysis = await SitemapAnalysisFactory.create(severity=SitemapAnalysis.Severity.WARNING.value)

    assert (
        await repository.filter(severity=SitemapAnalysis.Severity.WARNING.value).first() == analysis
    )


@pytest.mark.asyncio
async def test_sitemap_analysis_repository_checks_existence_with_filters() -> None:
    repository = SitemapAnalysisRepository()
    await SitemapAnalysisFactory.create(severity=SitemapAnalysis.Severity.CRITICAL.value)

    assert await repository.exists(severity=SitemapAnalysis.Severity.CRITICAL.value) is True
    assert await repository.exists(severity=SitemapAnalysis.Severity.INFO.value) is False


@pytest.mark.asyncio
async def test_sitemap_analysis_repository_creates_with_contract_and_returns_output() -> None:
    repository = SitemapAnalysisRepository()

    analysis = await repository.create(
        SitemapAnalysisIn(
            analysis_date=date(2026, 5, 19),
            status="succeeded",
            root_sitemap_url="https://example.com/sitemap.xml",
            summary="Sitemap analysis service is ready.",
        )
    )

    assert isinstance(analysis, SitemapAnalysisOut)
    assert analysis.id > 0
    assert analysis.analysis_date == date(2026, 5, 19)
    assert analysis.root_sitemap_url == "https://example.com/sitemap.xml"


@pytest.mark.asyncio
async def test_sitemap_analysis_repository_updates_contract_with_kwargs() -> None:
    repository = SitemapAnalysisRepository()
    analysis = await repository.create(
        SitemapAnalysisIn(
            analysis_date=date(2026, 5, 19),
            status="running",
            root_sitemap_url="https://example.com/sitemap.xml",
            summary="Sitemap analysis started.",
        )
    )

    updated = await repository.update(
        analysis,
        status="succeeded",
        summary="Sitemap analysis service is ready.",
    )

    assert updated.id == analysis.id
    assert updated.status == "succeeded"
    assert updated.summary == "Sitemap analysis service is ready."


@pytest.mark.asyncio
async def test_sitemap_analysis_repository_operator_filters() -> None:
    repository = SitemapAnalysisRepository()
    older_report = await SitemapAnalysisFactory.create(
        analysis_date=date(2026, 5, 17),
        status="succeeded",
        summary="Older sitemap report.",
        email_sent=True,
    )
    failed_report = await SitemapAnalysisFactory.create(
        analysis_date=date(2026, 5, 18),
        status="failed",
        severity=SitemapAnalysis.Severity.CRITICAL.value,
        summary="Failed sitemap report.",
        email_sent=True,
    )
    unsent_report = await SitemapAnalysisFactory.create(
        analysis_date=date(2026, 5, 19),
        status="succeeded",
        severity=SitemapAnalysis.Severity.WARNING.value,
        summary="Sitemap email is pending.",
        email_sent=False,
    )

    recent_reports = await repository.recent_reports(limit=2)
    failed_reports = await repository.failed_reports(limit=5)
    unsent_emails = await repository.unsent_emails(limit=5)

    assert [report.id for report in recent_reports] == [unsent_report.id, failed_report.id]
    assert older_report.id not in [report.id for report in recent_reports]
    assert [report.id for report in failed_reports] == [failed_report.id]
    assert [report.id for report in unsent_emails] == [unsent_report.id]


@pytest.mark.asyncio
async def test_sitemap_analysis_repository_deletes_retention_candidates() -> None:
    repository = SitemapAnalysisRepository()
    today = date.today()
    old_report = await SitemapAnalysisFactory.create(
        analysis_date=today - timedelta(days=60),
        status="succeeded",
        summary="Old sitemap report.",
        email_sent=True,
    )
    recent_report = await SitemapAnalysisFactory.create(
        analysis_date=today - timedelta(days=2),
        status="succeeded",
        summary="Recent sitemap report.",
        email_sent=True,
    )

    candidate_ids = await repository.retention_candidate_ids(older_than_days=30)
    deleted_count = await repository.delete_retention_candidates(older_than_days=30)

    assert candidate_ids == [old_report.id]
    assert deleted_count == 1
    assert await SitemapAnalysis.filter(id=old_report.id).exists() is False
    assert await SitemapAnalysis.filter(id=recent_report.id).exists() is True


@pytest.mark.asyncio
async def test_log_analysis_llm_call_repository_creates_steps() -> None:
    repository = LLMCallRepository(trace_id="trace-1")

    await repository.create(
        LogAnalysisLLMCallIn(
            analysis_date=date(2026, 5, 19),
            workflow_name="analyze_daily_log_bundle",
            iteration=1,
            step_type="llm_call",
            action="call_tools",
            llm_response_text='{"action": "call_tools"}',
        )
    )
    await repository.create(
        LogAnalysisLLMCallIn(
            trace_id="trace-2",
            step_type="llm_call",
            action="final_report",
        )
    )
    await repository.create(
        LogAnalysisLLMCallIn(
            analysis_date=date(2026, 5, 19),
            workflow_name="analyze_daily_log_bundle",
            iteration=1,
            step_type="mcp_tool_call",
            tool_name="inspect_proxy_activity",
            arguments_hash="abc123",
            arguments_text='{"project_name": "demo-shop"}',
            status="succeeded",
            started_at=datetime(2026, 5, 19, 12, tzinfo=UTC),
            finished_at=datetime(2026, 5, 19, 12, 0, 1, tzinfo=UTC),
            duration_ms=1000,
        )
    )

    steps: list[LogAnalysisLLMCall] = await LogAnalysisLLMCall.objects.filter(
        trace_id="trace-1"
    ).order_by("created_at", "id")

    assert [step.step_type for step in steps] == ["llm_call", "mcp_tool_call"]
    assert steps[0].action == "call_tools"
    assert steps[0].llm_response_text == '{"action": "call_tools"}'
    assert steps[1].tool_name == "inspect_proxy_activity"
    assert steps[1].status == "succeeded"
    assert steps[1].arguments_text == '{"project_name": "demo-shop"}'
