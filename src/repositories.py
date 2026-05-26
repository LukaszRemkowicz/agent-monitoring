from __future__ import annotations

from datetime import date
from typing import Any

from tortoise.queryset import QuerySet

from db.models import (
    LogAnalysis,
    LogAnalysisLLMCall,
    RunStatus,
    SitemapAnalysis,
)
from logging_config import get_logger
from schemas import (
    LogAnalysisIn,
    LogAnalysisLLMCallIn,
    LogAnalysisLLMCallOut,
    LogAnalysisOut,
    SitemapAnalysisIn,
    SitemapAnalysisOut,
)

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

    async def create(self, data: LogAnalysisIn) -> LogAnalysisOut:
        logger.debug(
            "creating log analysis",
            extra={
                "event": "log_analysis_repository_create",
                "analysis_date": str(data.analysis_date),
            },
        )
        analysis = await self.model.objects.create(**data.model_dump())
        return LogAnalysisOut.from_model(analysis)

    async def update(self, analysis: LogAnalysisOut, **updates: Any) -> LogAnalysisOut:
        data = analysis.model_dump(exclude={"id", "created_at"})
        data.update(updates)
        update_contract = LogAnalysisIn.model_validate(data)
        analysis_model = await self.model.get(id=analysis.id)
        update_data = update_contract.model_dump()
        for field_name, value in update_data.items():
            setattr(analysis_model, field_name, value)
        await analysis_model.save(update_fields=list(update_data))
        return LogAnalysisOut.from_model(analysis_model)

    async def get_by_date(self, analysis_date: date) -> LogAnalysisOut | None:
        logger.debug(
            "fetching log analysis by date",
            extra={
                "event": "log_analysis_repository_get_by_date",
                "analysis_date": str(analysis_date),
            },
        )
        analysis = await self.model.objects.filter_by_date(analysis_date).first()
        if analysis is None:
            return None
        return LogAnalysisOut.from_model(analysis)

    async def last_5_days(self, analysis_date: date) -> list[LogAnalysisOut]:
        """Return successful analyses from the last five days excluding this date."""

        logger.debug(
            "fetching last five days of log analyses",
            extra={
                "event": "log_analysis_repository_last_5_days",
                "analysis_date": str(analysis_date),
            },
        )
        analyses: list[LogAnalysis] = await self.model.objects.last_5_days(
            exclude_date=analysis_date
        ).filter(status=RunStatus.SUCCEEDED.value)
        return [LogAnalysisOut.from_model(analysis) for analysis in analyses]


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

    async def create(self, data: SitemapAnalysisIn) -> SitemapAnalysisOut:
        logger.debug(
            "creating sitemap analysis",
            extra={
                "event": "sitemap_analysis_repository_create",
                "analysis_date": str(data.analysis_date),
            },
        )
        analysis = await self.model.objects.create(**data.model_dump())
        return SitemapAnalysisOut.from_model(analysis)

    async def update(self, analysis: SitemapAnalysisOut, **updates: Any) -> SitemapAnalysisOut:
        data = analysis.model_dump(exclude={"id", "created_at"})
        data.update(updates)
        update_contract = SitemapAnalysisIn.model_validate(data)
        analysis_model = await self.model.get(id=analysis.id)
        update_data = update_contract.model_dump()
        for field_name, value in update_data.items():
            setattr(analysis_model, field_name, value)
        await analysis_model.save(update_fields=list(update_data))
        return SitemapAnalysisOut.from_model(analysis_model)

    async def get_by_date(self, analysis_date: date) -> SitemapAnalysisOut | None:
        logger.debug(
            "fetching sitemap analysis by date",
            extra={
                "event": "sitemap_analysis_repository_get_by_date",
                "analysis_date": str(analysis_date),
            },
        )
        analysis = await self.model.objects.filter_by_date(analysis_date).first()
        if analysis is None:
            return None
        return SitemapAnalysisOut.from_model(analysis)


class LLMCallRepository:
    """Database access boundary for persisted LLM/tool-loop steps."""

    model: type[LogAnalysisLLMCall] = LogAnalysisLLMCall

    def __init__(self, *, trace_id: str = "") -> None:
        self.trace_id = trace_id

    async def create(
        self,
        data: LogAnalysisLLMCallIn,
    ) -> LogAnalysisLLMCallOut:
        create_data = data
        if self.trace_id and not data.trace_id:
            create_data = data.model_copy(update={"trace_id": self.trace_id})
        step = await self.model.objects.create(**create_data.model_dump())
        return LogAnalysisLLMCallOut.from_model(step)
