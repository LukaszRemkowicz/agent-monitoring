from __future__ import annotations

import pytest

from services.sitemap import (
    Crawler,
    SitemapHTTPResponse,
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
