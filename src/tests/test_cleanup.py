from __future__ import annotations

from datetime import date, timedelta

import pytest

from db.models import LogAnalysis, LogAnalysisLLMCall, SitemapAnalysis
from schemas import LogAnalysisLLMCallIn
from services.cleanup import MonitoringCleanupService
from tests.factories import LogAnalysisFactory, SitemapAnalysisFactory


@pytest.mark.asyncio
async def test_cleanup_reports_does_not_delete_llm_call_audit_rows() -> None:
    today = date.today()
    old_llm_call = await LogAnalysisLLMCall.objects.create(
        **LogAnalysisLLMCallIn(
            analysis_date=today - timedelta(days=60),
            trace_id="audit-trace",
            step_type="llm_call",
            action="final_report",
        ).model_dump()
    )
    await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=60),
        status="failed",
        summary="Old log report.",
        email_sent=True,
    )
    await SitemapAnalysisFactory.create(
        analysis_date=today - timedelta(days=60),
        status="succeeded",
        summary="Old sitemap report.",
        email_sent=True,
    )

    result = await MonitoringCleanupService().cleanup_reports(
        log_retention_days=5,
        sitemap_retention_days=5,
        protected_log_history_count=5,
        dry_run=False,
    )

    assert result["counts"] == {
        "log_analyses": 1,
        "sitemap_analyses": 1,
    }
    assert result["total"] == 2
    assert await LogAnalysisLLMCall.filter(id=old_llm_call.id).exists() is True


@pytest.mark.asyncio
async def test_cleanup_reports_uses_separate_log_and_sitemap_retention_days() -> None:
    today = date.today()
    old_log_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=20),
        status="failed",
        summary="Old failed log report.",
        email_sent=True,
    )
    newer_log_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=5),
        status="failed",
        summary="Newer failed log report.",
        email_sent=True,
    )
    old_sitemap_report = await SitemapAnalysisFactory.create(
        analysis_date=today - timedelta(days=8),
        status="succeeded",
        summary="Old sitemap report.",
        email_sent=True,
    )
    newer_sitemap_report = await SitemapAnalysisFactory.create(
        analysis_date=today - timedelta(days=3),
        status="succeeded",
        summary="Newer sitemap report.",
        email_sent=True,
    )

    result = await MonitoringCleanupService().cleanup_reports(
        log_retention_days=10,
        sitemap_retention_days=5,
        protected_log_history_count=5,
        dry_run=False,
    )

    assert result["retention_days"] == {
        "log_analyses": 10,
        "sitemap_analyses": 5,
    }
    assert result["counts"] == {
        "log_analyses": 1,
        "sitemap_analyses": 1,
    }
    assert result["total"] == 2
    assert await LogAnalysis.filter(id=old_log_report.id).exists() is False
    assert await LogAnalysis.filter(id=newer_log_report.id).exists() is True
    assert await SitemapAnalysis.filter(id=old_sitemap_report.id).exists() is False
    assert await SitemapAnalysis.filter(id=newer_sitemap_report.id).exists() is True


@pytest.mark.asyncio
async def test_cleanup_reports_does_not_extend_critical_failed_or_unsent_retention() -> None:
    today = date.today()
    critical_failed_report = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=30),
        status="failed",
        severity="CRITICAL",
        summary="Old critical failed log report.",
        email_sent=False,
    )
    unsent_sitemap_report = await SitemapAnalysisFactory.create(
        analysis_date=today - timedelta(days=30),
        status="failed",
        severity="CRITICAL",
        summary="Old critical unsent sitemap report.",
        email_sent=False,
    )

    result = await MonitoringCleanupService().cleanup_reports(
        log_retention_days=10,
        sitemap_retention_days=10,
        protected_log_history_count=5,
        dry_run=False,
    )

    assert result["counts"] == {
        "log_analyses": 1,
        "sitemap_analyses": 1,
    }
    assert await LogAnalysis.filter(id=critical_failed_report.id).exists() is False
    assert await SitemapAnalysis.filter(id=unsent_sitemap_report.id).exists() is False
