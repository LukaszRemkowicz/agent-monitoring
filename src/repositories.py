from __future__ import annotations

from datetime import date
from typing import Any

from tortoise.queryset import QuerySet

from db.models import LogAnalysis, SitemapAnalysis
from logging_config import get_logger

logger = get_logger(__name__)


class LogAnalysisRepository:
    """Database access boundary for log-analysis rows."""

    model: type[LogAnalysis] = LogAnalysis

    def filter(self, **filters: Any) -> QuerySet[LogAnalysis]:
        return self.model.objects.filter(**filters)

    async def exists(self, **filters: Any) -> bool:
        logger.debug(
            "checking log analysis existence",
            extra={
                "event": "log_analysis_repository_exists",
                "filters": filters,
            },
        )
        return await self.filter(**filters).exists()

    async def get_by_date(self, analysis_date: date) -> LogAnalysis | None:
        logger.debug(
            "fetching log analysis by date",
            extra={
                "event": "log_analysis_repository_get_by_date",
                "analysis_date": str(analysis_date),
            },
        )
        return await self.model.objects.filter_by_date(analysis_date).first()


class SitemapAnalysisRepository:
    """Database access boundary for sitemap-analysis rows."""

    model: type[SitemapAnalysis] = SitemapAnalysis

    def filter(self, **filters: Any) -> QuerySet[SitemapAnalysis]:
        return self.model.objects.filter(**filters)

    async def exists(self, **filters: Any) -> bool:
        logger.debug(
            "checking sitemap analysis existence",
            extra={
                "event": "sitemap_analysis_repository_exists",
                "filters": filters,
            },
        )
        return await self.filter(**filters).exists()

    async def get_by_date(self, analysis_date: date) -> SitemapAnalysis | None:
        logger.debug(
            "fetching sitemap analysis by date",
            extra={
                "event": "sitemap_analysis_repository_get_by_date",
                "analysis_date": str(analysis_date),
            },
        )
        return await self.model.objects.filter_by_date(analysis_date).first()
