from __future__ import annotations

import pytest

from db.models import LogAnalysis, SitemapAnalysis
from repositories import LogAnalysisRepository, SitemapAnalysisRepository
from tests.factories import LogAnalysisFactory, SitemapAnalysisFactory


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
