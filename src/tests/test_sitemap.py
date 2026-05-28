from __future__ import annotations

from typing import cast

import pytest
from llm_core.providers.mock import MockProvider
from llm_core.types import ResponseFormat, TextPart, Usage

from mcp import McpWorkflowClient
from schemas import WorkflowBootstrap
from services.sitemap import (
    Crawler,
    LLMSummaryBuilder,
    SitemapAuditReport,
    SitemapHTTPResponse,
    SitemapIssue,
    SitemapIssueCategory,
    build_sitemap_url,
)

pytestmark = pytest.mark.asyncio


class FakeSitemapHTTPClient:
    def __init__(
        self,
        responses: dict[tuple[str, bool], SitemapHTTPResponse],
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[str, bool]] = []

    async def get(self, url: str, *, allow_redirects: bool = True) -> SitemapHTTPResponse:
        self.calls.append((url, allow_redirects))
        return self.responses[(url, allow_redirects)]


class FakeSitemapWorkflowClient(McpWorkflowClient):
    def __init__(self) -> None:
        super().__init__(
            base_url="http://mcp.test/mcp",
            workflow_jwt="test-workflow-jwt",
        )
        self.calls = 0

    async def get_sitemap_workflow_bundle(self) -> WorkflowBootstrap:
        self.calls += 1
        return WorkflowBootstrap(
            workflow_name="analyze_sitemap_bundle",
            prompt=(
                "key_findings must be a list of complete strings. "
                "Do not return objects. "
                "recommendations must be one plain string. "
                "Use a self-referential canonical or remove that URL from the sitemap."
            ),
            mandatory_skills=[],
            optional_skills=[],
            tools=[],
        )


async def test_sitemap_audit_expands_nested_sitemaps_without_issues() -> None:
    client = FakeSitemapHTTPClient(
        {
            (
                "https://example.com/sitemap.xml",
                True,
            ): SitemapHTTPResponse(
                status_code=200,
                text=(
                    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    "<sitemap><loc>https://example.com/pages.xml</loc></sitemap>"
                    "</sitemapindex>"
                ),
                url="https://example.com/sitemap.xml",
                headers={},
            ),
            (
                "https://example.com/pages.xml",
                True,
            ): SitemapHTTPResponse(
                status_code=200,
                text=(
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    "<url><loc>https://example.com/</loc></url>"
                    "<url><loc>https://example.com/about</loc></url>"
                    "</urlset>"
                ),
                url="https://example.com/pages.xml",
                headers={},
            ),
            (
                "https://example.com/",
                False,
            ): SitemapHTTPResponse(
                status_code=200,
                text='<html><head><link rel="canonical" href="https://example.com/"></head></html>',
                url="https://example.com/",
                headers={},
            ),
            (
                "https://example.com/about",
                False,
            ): SitemapHTTPResponse(
                status_code=200,
                text="<html><head></head></html>",
                url="https://example.com/about",
                headers={},
            ),
        }
    )
    service = Crawler(
        client=client,
        sitemap_url="https://example.com/sitemap.xml",
        site_domain="example.com",
    )

    report = await service.audit()

    assert report.total_sitemaps == 2
    assert report.total_urls == 2
    assert report.issues == []


async def test_sitemap_audit_detects_deterministic_issues() -> None:
    client = FakeSitemapHTTPClient(
        {
            (
                "https://example.com/sitemap.xml",
                True,
            ): SitemapHTTPResponse(
                status_code=200,
                text=(
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    "<url><loc>https://example.com/old</loc></url>"
                    "<url><loc>https://example.com/old</loc></url>"
                    "<url><loc>https://example.com/missing</loc></url>"
                    "<url><loc>https://other.example/page</loc></url>"
                    "<url><loc>https://example.com/canonical</loc></url>"
                    "<url><loc>https://example.com/noindex</loc></url>"
                    "</urlset>"
                ),
                url="https://example.com/sitemap.xml",
                headers={},
            ),
            (
                "https://example.com/old",
                False,
            ): SitemapHTTPResponse(
                status_code=301,
                text="",
                url="https://example.com/old",
                headers={},
            ),
            (
                "https://example.com/old",
                True,
            ): SitemapHTTPResponse(
                status_code=200,
                text="<html></html>",
                url="https://example.com/new",
                headers={},
            ),
            (
                "https://example.com/missing",
                False,
            ): SitemapHTTPResponse(
                status_code=404,
                text="not found",
                url="https://example.com/missing",
                headers={},
            ),
            (
                "https://other.example/page",
                False,
            ): SitemapHTTPResponse(
                status_code=200,
                text="<html></html>",
                url="https://other.example/page",
                headers={},
            ),
            (
                "https://example.com/canonical",
                False,
            ): SitemapHTTPResponse(
                status_code=200,
                text=(
                    '<html><head><link rel="canonical" '
                    'href="https://example.com/preferred"></head></html>'
                ),
                url="https://example.com/canonical",
                headers={},
            ),
            (
                "https://example.com/noindex",
                False,
            ): SitemapHTTPResponse(
                status_code=200,
                text='<html><head><meta name="robots" content="noindex"></head></html>',
                url="https://example.com/noindex",
                headers={},
            ),
        }
    )
    service = Crawler(
        client=client,
        sitemap_url="https://example.com/sitemap.xml",
        site_domain="example.com",
    )

    report = await service.audit()

    categories = {issue.category for issue in report.issues}
    assert categories == {
        SitemapIssueCategory.DUPLICATE_URL,
        SitemapIssueCategory.REDIRECT_IN_SITEMAP,
        SitemapIssueCategory.FINAL_URL_MISMATCH,
        SitemapIssueCategory.BROKEN_URL,
        SitemapIssueCategory.NON_PROD_DOMAIN,
        SitemapIssueCategory.CANONICAL_MISMATCH,
        SitemapIssueCategory.NOINDEX_PAGE,
    }
    assert service.summarize_issues(report.issues) == {
        "broken_url": 1,
        "canonical_mismatch": 1,
        "duplicate_url": 1,
        "final_url_mismatch": 1,
        "noindex_page": 1,
        "non_prod_domain": 1,
        "redirect_in_sitemap": 1,
    }


async def test_build_sitemap_url_from_site_domain() -> None:
    assert build_sitemap_url("example.com") == "https://example.com/sitemap.xml"
    assert build_sitemap_url("https://example.com/") == "https://example.com/sitemap.xml"


async def test_llm_summary_builder_uses_provider_structured_output() -> None:
    provider = MockProvider()
    provider.queue_text_response(
        '{"summary":"LLM summary"}',
        structured_output={
            "summary": "LLM summary",
            "severity": "WARNING",
            "key_findings": ["One broken sitemap URL."],
            "recommendations": "Fix the broken URL.",
            "trend_summary": "More issues than yesterday.",
        },
        usage=Usage(total_tokens=42, cost_usd=0.0012),
    )
    workflow_client = FakeSitemapWorkflowClient()
    builder = LLMSummaryBuilder(llm_provider=provider, mcp_client=workflow_client)
    report = SitemapAuditReport(
        root_sitemap_url="https://example.com/sitemap.xml",
        total_sitemaps=1,
        total_urls=1,
        issues=[
            SitemapIssue(
                url="https://example.com/missing",
                category=SitemapIssueCategory.BROKEN_URL,
                message="URL returned an error status.",
                status_code=404,
            )
        ],
    )

    result = await builder.summarize(report, {"broken_url": 1})

    assert result == {
        "summary": "LLM summary",
        "severity": "WARNING",
        "key_findings": ["One broken sitemap URL."],
        "recommendations": "Fix the broken URL.",
        "trend_summary": "More issues than yesterday.",
        "gpt_tokens_used": 42,
        "gpt_cost_usd": 0.0012,
    }
    request = provider.requests[0]
    assert request.options.response_format == ResponseFormat.JSON_OBJECT
    assert request.metadata["workflow_name"] == "analyze_sitemap_bundle"
    system_part = cast(TextPart, request.messages[0].parts[0])
    assert "key_findings must be a list of complete strings" in system_part.text
    assert "Do not return objects" in system_part.text
    assert "recommendations must be one plain string" in system_part.text
    assert "self-referential canonical" in system_part.text
    assert "remove that URL from the sitemap" in system_part.text
    user_part = cast(TextPart, request.messages[1].parts[0])
    assert "https://example.com/missing" in user_part.text
    assert '"issue_count": 1' in user_part.text
    assert workflow_client.calls == 1


async def test_llm_summary_builder_normalizes_rich_provider_fields() -> None:
    provider = MockProvider()
    provider.queue_text_response(
        "{}",
        structured_output={
            "summary": "Canonical mismatch detected.",
            "severity": "WARNING",
            "key_findings": [
                {
                    "issue": "Canonical URL differs from the sitemap URL.",
                    "url": "https://example.com/astrophotography?page=2",
                    "canonical_url": "https://example.com/astrophotography",
                }
            ],
            "recommendations": [
                "Review the canonical URL.",
                "Avoid SEO confusion.",
            ],
            "trend_summary": "No previous sitemap trend was available.",
        },
    )
    builder = LLMSummaryBuilder(
        llm_provider=provider,
        mcp_client=FakeSitemapWorkflowClient(),
    )
    report = SitemapAuditReport(
        root_sitemap_url="https://example.com/sitemap.xml",
        total_sitemaps=1,
        total_urls=1,
        issues=[],
    )

    result = await builder.summarize(report, {})

    assert result["key_findings"] == [
        (
            "Canonical URL differs from the sitemap URL: "
            "https://example.com/astrophotography?page=2 "
            "(canonical URL: https://example.com/astrophotography)"
        )
    ]
    assert result["recommendations"] == "Review the canonical URL. Avoid SEO confusion."
