from __future__ import annotations

from typing import TypedDict

from repositories import LogAnalysisRepository, SitemapAnalysisRepository


class CleanupCounts(TypedDict):
    log_analyses: int
    sitemap_analyses: int


class CleanupRetentionDays(TypedDict):
    log_analyses: int
    sitemap_analyses: int


class CleanupReportsResult(TypedDict):
    retention_days: CleanupRetentionDays
    protected_log_history_count: int
    dry_run: bool
    counts: CleanupCounts
    total: int


class MonitoringCleanupService:
    """Coordinate local monitoring database retention cleanup."""

    def __init__(
        self,
        *,
        log_repository: LogAnalysisRepository | None = None,
        sitemap_repository: SitemapAnalysisRepository | None = None,
    ) -> None:
        self.log_repository = log_repository or LogAnalysisRepository()
        self.sitemap_repository = sitemap_repository or SitemapAnalysisRepository()

    async def cleanup_reports(
        self,
        *,
        log_retention_days: int,
        sitemap_retention_days: int,
        protected_log_history_count: int,
        dry_run: bool = True,
    ) -> CleanupReportsResult:
        """Return or delete report cleanup candidates for the configured cutoff."""

        if dry_run:
            counts = CleanupCounts(
                log_analyses=len(
                    await self.log_repository.retention_candidate_ids(
                        older_than_days=log_retention_days,
                        keep_recent_successful=protected_log_history_count,
                    )
                ),
                sitemap_analyses=len(
                    await self.sitemap_repository.retention_candidate_ids(
                        older_than_days=sitemap_retention_days,
                    )
                ),
            )
        else:
            counts = CleanupCounts(
                log_analyses=await self.log_repository.delete_retention_candidates(
                    older_than_days=log_retention_days,
                    keep_recent_successful=protected_log_history_count,
                ),
                sitemap_analyses=await self.sitemap_repository.delete_retention_candidates(
                    older_than_days=sitemap_retention_days,
                ),
            )
        total: int = counts["log_analyses"] + counts["sitemap_analyses"]
        return CleanupReportsResult(
            retention_days=CleanupRetentionDays(
                log_analyses=log_retention_days,
                sitemap_analyses=sitemap_retention_days,
            ),
            protected_log_history_count=protected_log_history_count,
            dry_run=dry_run,
            counts=counts,
            total=total,
        )
