from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from html.parser import HTMLParser
from time import monotonic
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from llm_core.protocols import LLMProvider
from llm_core.types import GenerationOptions, LLMRequest, LLMResponse, Message, ResponseFormat
from pydantic import BaseModel, BeforeValidator, Field

from db.models import RunStatus, SitemapAnalysis
from logging_config import get_logger
from mcp import McpWorkflowClient
from repositories import SitemapAnalysisRepository
from schemas import SitemapAnalysisIn, SitemapAnalysisOut, WorkflowBootstrap

logger = get_logger(__name__)


class SitemapIssueCategory(StrEnum):
    """Deterministic sitemap issue categories copied from landingpage."""

    REDIRECT_IN_SITEMAP = "redirect_in_sitemap"
    FINAL_URL_MISMATCH = "final_url_mismatch"
    DUPLICATE_URL = "duplicate_url"
    NON_PROD_DOMAIN = "non_prod_domain"
    FETCH_ERROR = "fetch_error"
    BROKEN_URL = "broken_url"
    CANONICAL_MISMATCH = "canonical_mismatch"
    NOINDEX_PAGE = "noindex_page"


@dataclass(frozen=True)
class SitemapHTTPResponse:
    """Small HTTP response shape needed by deterministic sitemap checks."""

    status_code: int
    text: str
    url: str
    headers: dict[str, str]


@dataclass(frozen=True)
class SitemapIssue:
    """One deterministic sitemap issue found before any LLM interpretation."""

    url: str
    category: SitemapIssueCategory
    message: str
    status_code: int | None = None
    final_url: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a stable database representation."""

        payload: dict[str, object] = {
            "url": self.url,
            "category": self.category.value,
            "message": self.message,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.final_url is not None:
            if self.category == SitemapIssueCategory.CANONICAL_MISMATCH:
                payload["canonical_url"] = self.final_url
            else:
                payload["final_url"] = self.final_url
        return payload


@dataclass(frozen=True)
class SitemapAuditReport:
    """Deterministic sitemap audit result stored by the monitoring app."""

    root_sitemap_url: str
    total_sitemaps: int
    total_urls: int
    issues: list[SitemapIssue]


def _normalize_llm_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(_normalize_llm_text(item) for item in value if item is not None).strip()
    if isinstance(value, dict):
        issue = value.get("issue")
        url = value.get("url")
        details = [
            f"{_humanize_detail_key(key)}: {item}"
            for key, item in value.items()
            if key not in {"issue", "url"} and item is not None
        ]
        if issue and url:
            issue_text = _normalize_llm_text(issue).rstrip(".:")
            suffix = f" ({', '.join(details)})" if details else ""
            return f"{issue_text}: {url}{suffix}"
        return json.dumps(value, sort_keys=True, default=str, ensure_ascii=True)
    return str(value).strip()


def _normalize_key_findings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_normalize_llm_text(item) for item in value]
    return [_normalize_llm_text(value)]


def _humanize_detail_key(key: object) -> str:
    if key == "canonical_url":
        return "canonical URL"
    if key == "final_url":
        return "final URL"
    if key == "status_code":
        return "status code"
    return str(key).replace("_", " ")


class SitemapSummaryPayload(BaseModel):
    """Validated LLM sitemap summary shape."""

    summary: Annotated[str, BeforeValidator(_normalize_llm_text)]
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    key_findings: Annotated[list[str], BeforeValidator(_normalize_key_findings)] = Field(
        default_factory=list
    )
    recommendations: Annotated[str, BeforeValidator(_normalize_llm_text)] = ""
    trend_summary: Annotated[str, BeforeValidator(_normalize_llm_text)] = ""


class SitemapCrawler(Protocol):
    """Boundary used by the sitemap analysis runner."""

    async def audit(self) -> SitemapAuditReport: ...

    def summarize_issues(self, issues: Iterable[SitemapIssue]) -> dict[str, int]: ...


class SitemapFetcher(Protocol):
    """HTTP boundary used by the sitemap crawler."""

    async def get(self, url: str, *, allow_redirects: bool = True) -> SitemapHTTPResponse: ...


class SitemapHTTPClient(SitemapFetcher):
    """Fetch sitemap and page URLs with httpx."""

    def __init__(self, *, timeout_seconds: float = 10.0, verify_ssl: bool = True) -> None:
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl

    async def get(self, url: str, *, allow_redirects: bool = True) -> SitemapHTTPResponse:
        """Fetch one URL and return the response shape used by the audit service."""

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            verify=self.verify_ssl,
            follow_redirects=allow_redirects,
        ) as client:
            response: httpx.Response = await client.get(url)
        return SitemapHTTPResponse(
            status_code=response.status_code,
            text=response.text,
            url=str(response.url),
            headers=dict(response.headers),
        )


class SitemapXMLParser:
    """Parse sitemap XML and return the root tag plus all location values."""

    @staticmethod
    def parse_locations(xml_text: str) -> tuple[str, list[str]]:
        root: ElementTree.Element = ElementTree.fromstring(xml_text)
        root_tag: str = SitemapXMLParser._strip_namespace(root.tag)
        locations: list[str] = []

        for element in root.iter():
            if SitemapXMLParser._strip_namespace(element.tag) != "loc":
                continue
            value: str = (element.text or "").strip()
            if value:
                locations.append(value)

        return root_tag, locations

    @staticmethod
    def _strip_namespace(tag_name: str) -> str:
        if "}" in tag_name:
            return tag_name.split("}", 1)[1]
        return tag_name


class HTMLMetadataParser(HTMLParser):
    """Extract canonical and robots metadata from fetched sitemap pages."""

    def __init__(self) -> None:
        super().__init__()
        self.canonical_href: str | None = None
        self.robots_directives: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes: dict[str, str] = {
            key.lower(): value for key, value in attrs if value is not None
        }

        if tag.lower() == "link" and self._is_canonical_link(attributes):
            href: str | None = attributes.get("href")
            if href and not self.canonical_href:
                self.canonical_href = href.strip()

        if tag.lower() != "meta":
            return

        metadata_name: str = attributes.get("name", "").strip().lower()
        if metadata_name != "robots":
            return

        content: str = attributes.get("content", "")
        directives: list[str] = [
            directive.strip().lower() for directive in content.split(",") if directive.strip()
        ]
        self.robots_directives.extend(directives)

    @staticmethod
    def _is_canonical_link(attributes: dict[str, str]) -> bool:
        rel_value: str = attributes.get("rel", "")
        rel_tokens: set[str] = {
            token.strip().lower() for token in rel_value.split() if token.strip()
        }
        return "canonical" in rel_tokens


class Crawler:
    """Deterministically fetch, expand, and audit a sitemap."""

    def __init__(self, *, client: SitemapFetcher, sitemap_url: str, site_domain: str) -> None:
        self.client = client
        self.sitemap_url = sitemap_url
        self.sitemap_hostname = _hostname_from_site_domain(site_domain)

    async def audit(self) -> SitemapAuditReport:
        """Return deterministic sitemap facts and issues."""

        urls, total_sitemaps = await self.collect_urls(self.sitemap_url)
        issues: list[SitemapIssue] = await self._audit_urls(urls)
        return SitemapAuditReport(
            root_sitemap_url=self.sitemap_url,
            total_sitemaps=total_sitemaps,
            total_urls=len(urls),
            issues=issues,
        )

    async def collect_urls(self, root_sitemap_url: str) -> tuple[list[str], int]:
        """Expand nested sitemap indexes into URL entries."""

        pending_sitemaps: list[str] = [root_sitemap_url]
        seen_sitemaps: set[str] = set()
        discovered_urls: list[str] = []

        while pending_sitemaps:
            current_sitemap_url: str = pending_sitemaps.pop(0)
            if current_sitemap_url in seen_sitemaps:
                continue
            seen_sitemaps.add(current_sitemap_url)

            response: SitemapHTTPResponse = await self.client.get(current_sitemap_url)
            root_tag, locations = SitemapXMLParser.parse_locations(response.text)
            if root_tag == "sitemapindex":
                pending_sitemaps.extend(locations)
                continue
            if root_tag == "urlset":
                discovered_urls.extend(locations)

        return discovered_urls, len(seen_sitemaps)

    @staticmethod
    def summarize_issues(issues: Iterable[SitemapIssue]) -> dict[str, int]:
        """Return issue counts by category."""

        issue_counts: dict[str, int] = {}
        for issue in issues:
            category: str = issue.category
            issue_counts[category] = issue_counts.get(category, 0) + 1
        return dict(sorted(issue_counts.items()))

    async def _audit_urls(self, urls: list[str]) -> list[SitemapIssue]:
        issues: list[SitemapIssue] = []
        duplicates: set[str] = self._find_duplicates(urls)

        for duplicate_url in sorted(duplicates):
            issues.append(
                SitemapIssue(
                    url=duplicate_url,
                    category=SitemapIssueCategory.DUPLICATE_URL,
                    message="URL appears more than once in sitemap output.",
                )
            )

        unique_urls: list[str] = list(dict.fromkeys(urls))
        for url in unique_urls:
            issues.extend(await self._audit_single_url(url))

        return issues

    async def _audit_single_url(self, url: str) -> list[SitemapIssue]:
        issues: list[SitemapIssue] = []
        hostname: str = urlparse(url).netloc
        if hostname != self.sitemap_hostname:
            issues.append(
                SitemapIssue(
                    url=url,
                    category=SitemapIssueCategory.NON_PROD_DOMAIN,
                    message="URL host does not match the production domain.",
                )
            )

        try:
            response: SitemapHTTPResponse = await self.client.get(url, allow_redirects=False)
        except httpx.HTTPError as error:
            issues.append(
                SitemapIssue(
                    url=url,
                    category=SitemapIssueCategory.FETCH_ERROR,
                    message=f"Request failed: {error}",
                )
            )
            return issues

        status_code: int = response.status_code
        final_url: str = response.url

        if status_code >= 400:
            issues.append(
                SitemapIssue(
                    url=url,
                    category=SitemapIssueCategory.BROKEN_URL,
                    message="URL returned an error status.",
                    status_code=status_code,
                    final_url=final_url,
                )
            )
            return issues

        if status_code >= 300:
            followed_response: SitemapHTTPResponse = await self.client.get(
                url,
                allow_redirects=True,
            )
            final_url = followed_response.url
            issues.append(
                SitemapIssue(
                    url=url,
                    category=SitemapIssueCategory.REDIRECT_IN_SITEMAP,
                    message="Sitemap URL redirects instead of resolving directly.",
                    status_code=status_code,
                    final_url=final_url,
                )
            )
            issues.append(
                SitemapIssue(
                    url=url,
                    category=SitemapIssueCategory.FINAL_URL_MISMATCH,
                    message="Final URL differs from the sitemap URL.",
                    status_code=status_code,
                    final_url=final_url,
                )
            )

        if status_code == 200:
            issues.extend(self._audit_page_metadata(url, response))

        return issues

    def _audit_page_metadata(self, url: str, response: SitemapHTTPResponse) -> list[SitemapIssue]:
        issues: list[SitemapIssue] = []
        parser = HTMLMetadataParser()
        parser.feed(response.text)

        canonical_href: str | None = parser.canonical_href
        if canonical_href:
            canonical_url: str = urljoin(url, canonical_href)
            if canonical_url != url:
                issues.append(
                    SitemapIssue(
                        url=url,
                        category=SitemapIssueCategory.CANONICAL_MISMATCH,
                        message="Canonical URL differs from the sitemap URL.",
                        status_code=response.status_code,
                        final_url=canonical_url,
                    )
                )

        robot_directives: list[str] = parser.robots_directives + self._parse_x_robots_tag(response)
        if "noindex" in robot_directives:
            issues.append(
                SitemapIssue(
                    url=url,
                    category=SitemapIssueCategory.NOINDEX_PAGE,
                    message="Page is marked as noindex.",
                    status_code=response.status_code,
                )
            )

        return issues

    @staticmethod
    def _parse_x_robots_tag(response: SitemapHTTPResponse) -> list[str]:
        header_value = ""
        for key, value in response.headers.items():
            if key.lower() == "x-robots-tag":
                header_value = value
                break
        return [
            directive.strip().lower() for directive in header_value.split(",") if directive.strip()
        ]

    @staticmethod
    def _find_duplicates(urls: list[str]) -> set[str]:
        counts: Counter[str] = Counter(urls)
        return {url for url, count in counts.items() if count > 1}


class LLMSummaryBuilder:
    """Summarize deterministic sitemap audit facts with the configured LLM."""

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        mcp_client: McpWorkflowClient,
    ) -> None:
        self.llm_provider = llm_provider
        self.mcp_client = mcp_client

    async def summarize(
        self,
        report: SitemapAuditReport,
        issue_summary: dict[str, int],
    ) -> dict[str, object]:
        """Return summary fields for the persisted sitemap analysis."""

        workflow: WorkflowBootstrap = await self.mcp_client.get_sitemap_workflow_bundle()
        response: LLMResponse = self.llm_provider.generate(
            LLMRequest(
                messages=(
                    Message.from_text(
                        "system",
                        workflow.prompt,
                    ),
                    Message.from_text(
                        "user",
                        json.dumps(
                            {
                                "root_sitemap_url": report.root_sitemap_url,
                                "total_sitemaps": report.total_sitemaps,
                                "total_urls": report.total_urls,
                                "issue_count": len(report.issues),
                                "issue_summary": issue_summary,
                                "issues": [issue.as_dict() for issue in report.issues],
                            },
                            sort_keys=True,
                            ensure_ascii=True,
                        ),
                    ),
                ),
                options=GenerationOptions(
                    temperature=0.0,
                    response_format=ResponseFormat.JSON_OBJECT,
                ),
                metadata={
                    "workflow_name": workflow.workflow_name,
                    "phase": "summary",
                },
            )
        )
        payload: Any = response.structured_output
        if payload is None and response.text is not None:
            payload = json.loads(response.text)
        summary: SitemapSummaryPayload = SitemapSummaryPayload.model_validate(payload)
        return {
            **summary.model_dump(),
            "gpt_tokens_used": response.usage.total_tokens if response.usage else 0,
            "gpt_cost_usd": (
                response.usage.cost_usd if response.usage and response.usage.cost_usd else 0.0
            ),
        }


class AnalysisRunner:
    """Business service for deterministic sitemap-analysis command flow."""

    def __init__(
        self,
        *,
        repository: SitemapAnalysisRepository,
        sitemap_url: str,
        crawler: SitemapCrawler,
        summary_builder: LLMSummaryBuilder,
    ) -> None:
        self.repository = repository
        self.sitemap_url = sitemap_url
        self.crawler = crawler
        self.summary_builder = summary_builder

    async def run(
        self,
        *,
        analysis_date: date,
        force: bool,
    ) -> SitemapAnalysisOut:
        """Run deterministic sitemap analysis and persist the report."""

        execution_started_at: float = monotonic()
        logger.info(
            "preparing sitemap-analysis workflow",
            extra={
                "event": "sitemap_analysis_workflow_prepare_start",
                "analysis_date": str(analysis_date),
                "sitemap_url": self.sitemap_url,
                "force": force,
            },
        )
        existing: SitemapAnalysisOut | None = await self.repository.get_by_date(analysis_date)
        if existing is not None and not force:
            logger.info(
                "sitemap analysis already exists for analysis date",
                extra={
                    "event": "sitemap_analysis_workflow_prepare_skipped",
                    "analysis_date": str(analysis_date),
                    "sitemap_url": self.sitemap_url,
                    "reason": "existing_analysis",
                },
            )
            return existing

        started_at = datetime.now(UTC)
        try:
            fetch_started_at: float = monotonic()
            report: SitemapAuditReport = await self.crawler.audit()
            fetch_duration_seconds: float = round(monotonic() - fetch_started_at, 3)
            issue_summary: dict[str, int] = self.crawler.summarize_issues(report.issues)
            summary_fields: dict[str, object] = await self.summary_builder.summarize(
                report,
                issue_summary,
            )
            execution_time_seconds: float = round(monotonic() - execution_started_at, 3)
            completed_analysis = SitemapAnalysisIn.model_validate(
                {
                    "analysis_date": analysis_date,
                    "status": RunStatus.SUCCEEDED,
                    "started_at": started_at,
                    "finished_at": datetime.now(UTC),
                    "fetch_duration_seconds": fetch_duration_seconds,
                    "root_sitemap_url": report.root_sitemap_url,
                    "total_sitemaps": report.total_sitemaps,
                    "total_urls": report.total_urls,
                    "issue_summary": issue_summary,
                    "issues": [issue.as_dict() for issue in report.issues],
                    "execution_time_seconds": execution_time_seconds,
                    **summary_fields,
                }
            )
            updated_analysis: SitemapAnalysisOut = await self.repository.update_or_create(
                existing=existing,
                data=completed_analysis,
            )
        except Exception as exc:
            execution_time_seconds = round(monotonic() - execution_started_at, 3)
            logger.error(
                "sitemap-analysis workflow failed",
                extra={
                    "event": "sitemap_analysis_workflow_failed",
                    "analysis_date": str(analysis_date),
                    "sitemap_url": self.sitemap_url,
                    "failure_stage": "sitemap_analysis",
                    "execution_time_seconds": execution_time_seconds,
                    "error": str(exc),
                },
            )
            failed_analysis_input = SitemapAnalysisIn(
                analysis_date=analysis_date,
                status=RunStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                failure_stage="sitemap_analysis",
                root_sitemap_url=self.sitemap_url,
                summary="Sitemap analysis failed.",
                severity=SitemapAnalysis.Severity.CRITICAL,
                recommendations="Inspect the stored error message and retry the sitemap job.",
                error_message=str(exc),
                execution_time_seconds=execution_time_seconds,
            )
            failed_analysis: SitemapAnalysisOut = await self.repository.update_or_create(
                existing=existing,
                data=failed_analysis_input,
            )
            return failed_analysis
        logger.info(
            "prepared sitemap-analysis workflow",
            extra={
                "event": "sitemap_analysis_workflow_prepare_done",
                "analysis_date": str(analysis_date),
                "sitemap_url": self.sitemap_url,
                "severity": updated_analysis.severity,
                "issue_count": len(updated_analysis.issues),
                "execution_time_seconds": updated_analysis.execution_time_seconds,
            },
        )
        return updated_analysis


def build_sitemap_url(site_domain: str) -> str:
    """Return the root sitemap URL for the configured site domain."""

    site_origin: str = _site_origin(site_domain)
    return f"{site_origin}/sitemap.xml"


def _hostname_from_site_domain(site_domain: str) -> str:
    return urlparse(_site_origin(site_domain)).netloc


def _site_origin(site_domain: str) -> str:
    normalized_domain: str = site_domain.strip().rstrip("/")
    if "://" in normalized_domain:
        return normalized_domain
    return f"https://{normalized_domain}"
