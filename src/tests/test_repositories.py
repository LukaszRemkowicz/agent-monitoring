from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from db.models import LogAnalysis, LogAnalysisLLMCall, SitemapAnalysis
from repositories import (
    LLMCallRepository,
    LogAnalysisRepository,
    SitemapAnalysisRepository,
)
from schemas import (
    LogAnalysisIn,
    LogAnalysisLLMCallIn,
    LogAnalysisOut,
    SitemapAnalysisIn,
    SitemapAnalysisOut,
)
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
            arguments_text='{"project_name": "landingpage"}',
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
    assert steps[1].arguments_text == '{"project_name": "landingpage"}'
