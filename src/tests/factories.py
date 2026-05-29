from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import factory

from db.models import LogAnalysis, RunStatus, SitemapAnalysis


class TortoiseModelFactory(factory.Factory):
    class Meta:
        abstract = True

    @classmethod
    async def create(cls, **kwargs: Any) -> Any:
        instance = cls.build(**kwargs)
        await instance.save()
        return instance


class LogAnalysisFactory(TortoiseModelFactory):
    class Meta:
        model = LogAnalysis

    analysis_date = factory.Sequence(lambda n: date(2026, 5, 19) + timedelta(days=n))
    mcp_artifact = factory.LazyFunction(dict)
    status = RunStatus.PENDING
    started_at = None
    finished_at = None
    failure_stage = None
    log_window_since = None
    log_window_until = None
    mcp_collect_logs_id = None
    summary = "No critical issues."
    severity = LogAnalysis.Severity.INFO.value
    key_findings = factory.LazyFunction(list)
    recommendations = ""
    trend_summary = ""
    deterministic_fingerprint = factory.LazyFunction(dict)
    evidence_fingerprints = factory.LazyFunction(list)
    known_patterns = factory.LazyFunction(list)
    coverage_snapshot = factory.LazyFunction(dict)
    fingerprint_version = ""
    execution_time_seconds = 0.0
    gpt_tokens_used = 0
    gpt_cost_usd = 0.0
    email_sent = False
    error_message = ""


class SitemapAnalysisFactory(TortoiseModelFactory):
    class Meta:
        model = SitemapAnalysis

    analysis_date = factory.Sequence(lambda n: date(2026, 5, 19) + timedelta(days=n))
    status = RunStatus.PENDING
    started_at = None
    finished_at = None
    failure_stage = None
    fetch_duration_seconds = 0.0
    root_sitemap_url = "https://example.com/sitemap.xml"
    total_sitemaps = 0
    total_urls = 0
    issue_summary = factory.LazyFunction(dict)
    issues = factory.LazyFunction(list)
    summary = "Sitemap looks healthy."
    severity = SitemapAnalysis.Severity.INFO.value
    key_findings = factory.LazyFunction(list)
    recommendations = ""
    trend_summary = ""
    execution_time_seconds = 0.0
    gpt_tokens_used = 0
    gpt_cost_usd = 0.0
    email_sent = False
    error_message = ""
