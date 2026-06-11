from __future__ import annotations

from datetime import date, timedelta

import pytest

from db.models import LogAnalysisLLMCall
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
        retention_days=5,
        dry_run=False,
    )

    assert result["counts"] == {
        "log_analyses": 1,
        "sitemap_analyses": 1,
    }
    assert result["total"] == 2
    assert await LogAnalysisLLMCall.filter(id=old_llm_call.id).exists() is True
