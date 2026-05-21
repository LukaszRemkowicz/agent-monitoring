from __future__ import annotations

from datetime import date, timedelta

import pytest

from db.models import (
    LogAnalysis,
    LogAnalysisManager,
    LogAnalysisQuerySet,
    SitemapAnalysis,
    SitemapAnalysisManager,
    SitemapAnalysisQuerySet,
)
from tests.factories import LogAnalysisFactory, SitemapAnalysisFactory


@pytest.mark.asyncio
async def test_models_expose_django_style_objects_manager() -> None:
    analysis = await LogAnalysisFactory.create()

    found = await LogAnalysis.objects.get(id=analysis.id)
    queryset = LogAnalysis.objects.filter(severity=LogAnalysis.Severity.INFO.value)
    all_analyses = await LogAnalysis.objects.all()

    assert found == analysis
    assert await queryset.count() == 1
    assert all_analyses == [analysis]


def test_model_fields_include_descriptions() -> None:
    assert (
        LogAnalysis._meta.fields_map["mcp_artifact"].description
        == "Opaque collect_logs artifact payload returned by MCP."
    )
    assert (
        LogAnalysis._meta.fields_map["summary"].description == "LLM-generated log analysis summary."
    )
    assert (
        SitemapAnalysis._meta.fields_map["root_sitemap_url"].description
        == "Root sitemap URL inspected by the sitemap analysis job."
    )


def test_log_analysis_uses_mcp_artifact_as_source_of_truth() -> None:
    assert "mcp_artifact" in LogAnalysis._meta.fields_map
    assert "sources" not in LogAnalysis._meta.fields_map
    assert "email_deliveries" not in LogAnalysis._meta.fields_map
    assert "email_deliveries" not in SitemapAnalysis._meta.fields_map


def test_log_analysis_email_helpers_use_monitoring_settings() -> None:
    analysis = LogAnalysis(
        analysis_date=date(2026, 5, 19),
        severity=LogAnalysis.Severity.CRITICAL.value,
        execution_time_seconds=12.34,
    )

    assert analysis.get_email_subject() == "[DEV][CRITICAL] Daily Log Analysis - 2026-05-19"
    assert analysis.get_email_context() == {
        "environment": "dev",
        "monitoring_project": "landingpage",
        "log_analysis": analysis,
        "execution_time": "12.3",
    }


@pytest.mark.asyncio
async def test_log_analysis_can_mark_email_sent() -> None:
    analysis = await LogAnalysisFactory.create()

    await analysis.mark_email_sent()
    refreshed = await LogAnalysis.objects.get(id=analysis.id)

    assert analysis.email_sent is True
    assert refreshed.email_sent is True


@pytest.mark.asyncio
async def test_log_analysis_objects_use_domain_queryset() -> None:
    today = date.today()
    old_analysis = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=10),
        severity=LogAnalysis.Severity.WARNING.value,
        email_sent=False,
    )
    critical_analysis = await LogAnalysisFactory.create(
        analysis_date=today - timedelta(days=1),
        severity=LogAnalysis.Severity.CRITICAL.value,
        email_sent=False,
    )
    await LogAnalysisFactory.create(
        analysis_date=today,
        severity=LogAnalysis.Severity.INFO.value,
        email_sent=True,
    )

    queryset = LogAnalysis.objects.get_queryset()

    assert isinstance(LogAnalysis.objects, LogAnalysisManager)
    assert isinstance(queryset, LogAnalysisQuerySet)
    assert (
        await LogAnalysis.objects.filter_by_date(old_analysis.analysis_date).first() == old_analysis
    )
    assert await LogAnalysis.objects.older_than(5).count() == 1
    assert await LogAnalysis.objects.unsent_emails().count() == 2
    assert await LogAnalysis.objects.critical().first() == critical_analysis
    assert await queryset.by_severity(LogAnalysis.Severity.WARNING.value).first() == old_analysis
    assert await queryset.last_5_days(exclude_date=today).count() == 1


def test_sitemap_analysis_email_helpers_use_monitoring_settings() -> None:
    analysis = SitemapAnalysis(
        analysis_date=date(2026, 5, 19),
        severity=SitemapAnalysis.Severity.WARNING.value,
        execution_time_seconds=4.56,
        issue_summary={"broken_links": 2, "missing_lastmod": 1},
        issues=[{"url": "https://example.com/broken"}],
    )

    assert analysis.get_email_subject() == "[DEV][WARNING] Sitemap Analysis - 2026-05-19"
    assert analysis.issue_count == 1
    assert analysis.issue_summary_lines == ["broken links: 2", "missing lastmod: 1"]
    assert analysis.get_email_context() == {
        "environment": "dev",
        "monitoring_project": "landingpage",
        "sitemap_analysis": analysis,
        "execution_time": "4.6",
    }


@pytest.mark.asyncio
async def test_sitemap_analysis_can_mark_email_sent() -> None:
    analysis = await SitemapAnalysisFactory.create()

    await analysis.mark_email_sent()
    refreshed = await SitemapAnalysis.objects.get(id=analysis.id)

    assert analysis.email_sent is True
    assert refreshed.email_sent is True


@pytest.mark.asyncio
async def test_sitemap_analysis_objects_use_domain_queryset() -> None:
    today = date.today()
    old_analysis = await SitemapAnalysisFactory.create(
        analysis_date=today - timedelta(days=8),
        severity=SitemapAnalysis.Severity.WARNING.value,
        email_sent=False,
    )
    await SitemapAnalysisFactory.create(
        analysis_date=today,
        severity=SitemapAnalysis.Severity.INFO.value,
        email_sent=True,
    )

    queryset = SitemapAnalysis.objects.get_queryset()

    assert isinstance(SitemapAnalysis.objects, SitemapAnalysisManager)
    assert isinstance(queryset, SitemapAnalysisQuerySet)
    assert (
        await SitemapAnalysis.objects.filter_by_date(old_analysis.analysis_date).first()
        == old_analysis
    )
    assert await SitemapAnalysis.objects.older_than(5).count() == 1
    assert await SitemapAnalysis.objects.unsent_emails().first() == old_analysis
