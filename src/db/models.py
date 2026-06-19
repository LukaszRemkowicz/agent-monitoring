from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum
from typing import Any, ClassVar

from tortoise import fields
from tortoise.queryset import QuerySet

from utils.byte_size import format_byte_size
from utils.log_artifacts import collect_log_artifact_byte_count, decompress_json_mapping

from .managers import DatabaseModel, QuerySetManager


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EmailDelivery(DatabaseModel):
    """One monitoring email delivery attempt."""

    class ReportKind(StrEnum):
        LOG_ANALYSIS = "log_analysis"
        SITEMAP_ANALYSIS = "sitemap_analysis"
        MONITORING_FAILURE = "monitoring_failure"

    class RecipientTarget(StrEnum):
        LOG = "log"
        SITEMAP = "sitemap"
        FAILURE = "failure"

    class Status(StrEnum):
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    id = fields.IntField(primary_key=True)
    created_at = fields.DatetimeField(
        auto_now_add=True,
        db_index=True,
        description="UTC timestamp when this email delivery attempt was recorded.",
    )
    report_kind = fields.CharField(
        max_length=40,
        db_index=True,
        description="Report or notification kind this email attempted to deliver.",
    )
    report_id = fields.IntField(
        null=True,
        db_index=True,
        description="Stored report id when this delivery belongs to a report row.",
    )
    analysis_date = fields.DateField(
        null=True,
        db_index=True,
        description="Analysis date associated with this delivery attempt.",
    )
    recipient_target = fields.CharField(
        max_length=40,
        db_index=True,
        description="Recipient group used for this delivery attempt.",
    )
    recipients: fields.JSONField[list[str]] = fields.JSONField(
        default=list,
        description="Email recipients used for this attempt.",
    )
    subject = fields.TextField(
        default="",
        description="Rendered email subject used for this attempt.",
    )
    status = fields.CharField(
        max_length=20,
        db_index=True,
        description="Delivery attempt status.",
    )
    attempted_at = fields.DatetimeField(
        auto_now_add=True,
        db_index=True,
        description="UTC timestamp when this delivery was attempted.",
    )
    sent_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp when the delivery succeeded.",
    )
    provider_message_id = fields.CharField(
        max_length=255,
        null=True,
        description="Provider message id when available.",
    )
    error_message = fields.TextField(
        default="",
        description="Error captured when the delivery attempt failed.",
    )

    class Meta:
        table = "email_deliveries"
        ordering = ["-attempted_at", "-id"]


class LogAnalysisQuerySet(QuerySet["LogAnalysis"]):
    """Query helpers for log analyses."""

    def filter_by_date(self, analysis_date: date) -> QuerySet[LogAnalysis]:
        """Filter log analyses for a specific analysis date."""

        return self.filter(analysis_date=analysis_date)

    def older_than(self, days: int) -> QuerySet[LogAnalysis]:
        """Filter log analyses older than N days."""

        cutoff = date.today() - timedelta(days=days)
        return self.filter(analysis_date__lt=cutoff)

    def last_5_days(
        self,
        exclude_date: date | None = None,
    ) -> QuerySet[LogAnalysis]:
        """Return log analyses from the last five days, newest first."""

        cutoff = date.today() - timedelta(days=5)
        queryset = self.filter(analysis_date__gte=cutoff).order_by("-analysis_date")
        if exclude_date is not None:
            queryset = queryset.exclude(analysis_date=exclude_date)
        return queryset

    def unsent_emails(self) -> QuerySet[LogAnalysis]:
        """Filter log analyses where email has not been sent."""

        return self.filter(email_sent=False)

    def by_severity(self, severity: str) -> QuerySet[LogAnalysis]:
        """Filter log analyses by severity."""

        return self.filter(severity=severity)

    def critical(self) -> QuerySet[LogAnalysis]:
        """Filter critical severity log analyses."""

        return self.by_severity(LogAnalysis.Severity.CRITICAL)


class LogAnalysisManager(QuerySetManager["LogAnalysis", LogAnalysisQuerySet]):
    """Manager for log analysis queries."""

    def __init__(self) -> None:
        super().__init__(LogAnalysisQuerySet)

    def filter_by_date(self, analysis_date: date) -> QuerySet[LogAnalysis]:
        return self.get_queryset().filter_by_date(analysis_date)

    def older_than(self, days: int) -> QuerySet[LogAnalysis]:
        return self.get_queryset().older_than(days)

    def last_5_days(self, exclude_date: date | None = None) -> QuerySet[LogAnalysis]:
        return self.get_queryset().last_5_days(exclude_date=exclude_date)

    def unsent_emails(self) -> QuerySet[LogAnalysis]:
        return self.get_queryset().unsent_emails()

    def by_severity(self, severity: str) -> QuerySet[LogAnalysis]:
        return self.get_queryset().by_severity(severity)

    def critical(self) -> QuerySet[LogAnalysis]:
        return self.get_queryset().critical()


class SitemapAnalysisQuerySet(QuerySet["SitemapAnalysis"]):
    """Query helpers for sitemap analyses."""

    def filter_by_date(self, analysis_date: date) -> QuerySet[SitemapAnalysis]:
        """Filter sitemap analyses for a specific analysis date."""

        return self.filter(analysis_date=analysis_date)

    def older_than(self, days: int) -> QuerySet[SitemapAnalysis]:
        """Filter sitemap analyses older than N days."""

        cutoff = date.today() - timedelta(days=days)
        return self.filter(analysis_date__lt=cutoff)

    def last_5_days(
        self,
        exclude_date: date | None = None,
    ) -> QuerySet[SitemapAnalysis]:
        """Return sitemap analyses from the last five days, newest first."""

        cutoff = date.today() - timedelta(days=5)
        queryset = self.filter(analysis_date__gte=cutoff).order_by("-analysis_date")
        if exclude_date is not None:
            queryset = queryset.exclude(analysis_date=exclude_date)
        return queryset

    def unsent_emails(self) -> QuerySet[SitemapAnalysis]:
        """Filter sitemap analyses where email has not been sent."""

        return self.filter(email_sent=False)

    def by_severity(self, severity: str) -> QuerySet[SitemapAnalysis]:
        """Filter sitemap analyses by severity."""

        return self.filter(severity=severity)

    def critical(self) -> QuerySet[SitemapAnalysis]:
        """Filter critical severity sitemap analyses."""

        return self.by_severity(SitemapAnalysis.Severity.CRITICAL)


class SitemapAnalysisManager(QuerySetManager["SitemapAnalysis", SitemapAnalysisQuerySet]):
    """Manager for sitemap analysis queries."""

    def __init__(self) -> None:
        super().__init__(SitemapAnalysisQuerySet)

    def filter_by_date(self, analysis_date: date) -> QuerySet[SitemapAnalysis]:
        return self.get_queryset().filter_by_date(analysis_date)

    def older_than(self, days: int) -> QuerySet[SitemapAnalysis]:
        return self.get_queryset().older_than(days)

    def last_5_days(self, exclude_date: date | None = None) -> QuerySet[SitemapAnalysis]:
        return self.get_queryset().last_5_days(exclude_date=exclude_date)

    def unsent_emails(self) -> QuerySet[SitemapAnalysis]:
        return self.get_queryset().unsent_emails()

    def by_severity(self, severity: str) -> QuerySet[SitemapAnalysis]:
        return self.get_queryset().by_severity(severity)

    def critical(self) -> QuerySet[SitemapAnalysis]:
        return self.get_queryset().critical()


class LogAnalysis(DatabaseModel):
    """Stored log-analysis report and execution state."""

    class Severity(StrEnum):
        INFO = "INFO"
        WARNING = "WARNING"
        CRITICAL = "CRITICAL"

    objects: ClassVar[LogAnalysisManager] = LogAnalysisManager()

    id = fields.IntField(
        primary_key=True,
        description="Database-generated integer id for this log analysis.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        db_index=True,
        description="UTC timestamp when this log analysis row was created.",
    )
    analysis_date = fields.DateField(
        unique=True,
        db_index=True,
        description="Calendar date this log analysis represents.",
    )
    mcp_artifact: fields.JSONField[dict[str, Any]] = fields.JSONField(
        default=dict,
        description="Opaque collect_logs artifact payload returned by MCP.",
    )

    status = fields.CharField(
        max_length=20,
        default=RunStatus.PENDING.value,
        db_index=True,
        description="Execution status for this log analysis job.",
    )
    started_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp when this log analysis job started.",
    )
    finished_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp when this log analysis job finished.",
    )
    failure_stage = fields.CharField(
        max_length=80,
        null=True,
        description="Pipeline stage where this log analysis failed, if any.",
    )
    log_window_since = fields.DatetimeField(
        null=True,
        description="Start of the log collection time window requested from MCP.",
    )
    log_window_until = fields.DatetimeField(
        null=True,
        description="End of the log collection time window requested from MCP.",
    )
    mcp_collect_logs_id = fields.CharField(
        max_length=255,
        null=True,
        description="Stable MCP collect_logs artifact id when MCP returns one.",
    )
    summary = fields.TextField(description="LLM-generated log analysis summary.")
    severity = fields.CharField(
        max_length=10,
        default=Severity.INFO.value,
        db_index=True,
        description="LLM-classified severity for this log analysis.",
    )
    key_findings: fields.JSONField[list[str]] = fields.JSONField(
        default=list,
        description="List of important findings extracted from the log analysis.",
    )
    recommendations = fields.TextField(
        default="",
        description="LLM-generated operational recommendations.",
    )
    trend_summary = fields.TextField(
        default="",
        description="LLM-generated trend comparison against prior analyses.",
    )
    fingerprints: fields.JSONField[dict[str, Any]] = fields.JSONField(
        default=dict,
        source_field="deterministic_fingerprint",
        description="Compact fingerprints derived from MCP artifacts and tool results.",
    )
    evidence_fingerprints: fields.JSONField[list[str]] = fields.JSONField(
        default=list,
        description="Stable evidence fingerprints used for baseline comparison.",
    )
    known_patterns: fields.JSONField[list[dict[str, Any]]] = fields.JSONField(
        default=list,
        description="Known recurring log patterns available to future runs.",
    )
    coverage_snapshot: fields.JSONField[dict[str, Any]] = fields.JSONField(
        default=dict,
        description="Source coverage snapshot used to compare current and baseline runs.",
    )
    fingerprint_version = fields.CharField(
        max_length=40,
        default="",
        db_index=True,
        description="Version of the structured history fingerprint format.",
    )
    execution_time_seconds = fields.FloatField(
        default=0.0,
        description="Total wall-clock execution time for this log analysis job.",
    )
    gpt_tokens_used = fields.IntField(
        default=0,
        description="OpenAI token count used for this log analysis.",
    )
    gpt_cost_usd = fields.FloatField(
        default=0.0,
        description="Estimated OpenAI API cost in USD for this log analysis.",
    )
    email_sent = fields.BooleanField(
        default=False,
        db_index=True,
        description="Whether the log analysis email was sent.",
    )
    error_message = fields.TextField(
        default="",
        description="Error message captured when this log analysis failed.",
    )

    class Meta:
        table = "log_analyses"
        ordering = ["-analysis_date"]

    def __str__(self) -> str:
        return f"Log Analysis {self.analysis_date} ({self.severity})"

    @property
    def execution_time_formatted(self) -> str:
        """Return execution time formatted for reports."""

        return f"{self.execution_time_seconds:.1f}"

    @property
    def log_size(self) -> str:
        """Return collected MCP log artifact size for display."""

        return format_byte_size(
            collect_log_artifact_byte_count(decompress_json_mapping(self.mcp_artifact))
        )

    async def mark_email_sent(self) -> None:
        """Mark this log analysis email as sent."""

        self.email_sent = True
        await self.save(update_fields=["email_sent"])


class LogAnalysisLLMCall(DatabaseModel):
    """One persisted LLM/tool-loop decision from a log-analysis run."""

    id = fields.IntField(primary_key=True)
    trace_id = fields.CharField(
        max_length=64,
        db_index=True,
        description="Run-local trace id grouping LLM calls from one command execution.",
    )
    analysis_date = fields.DateField(
        null=True,
        db_index=True,
        description="Analysis date associated with this LLM call.",
    )
    workflow_name = fields.CharField(max_length=160, null=True, db_index=True)
    mcp_session_id = fields.CharField(max_length=255, null=True)
    iteration = fields.IntField(null=True, db_index=True)
    step_type = fields.CharField(
        max_length=80,
        db_index=True,
        description="Kind of agent-loop step, such as llm_action_received or mcp_tool_call.",
    )
    action = fields.CharField(max_length=80, null=True, db_index=True)
    tool_name = fields.CharField(max_length=160, null=True, db_index=True)
    skill_name = fields.CharField(max_length=160, null=True, db_index=True)
    requested_tool_names_text = fields.TextField(default="")
    requested_skill_names_text = fields.TextField(default="")
    arguments_hash = fields.CharField(max_length=64, null=True, db_index=True)
    arguments_text = fields.TextField(default="")
    status = fields.CharField(max_length=40, null=True, db_index=True)
    duplicate_skipped = fields.BooleanField(default=False, db_index=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)
    duration_ms = fields.IntField(null=True)
    llm_response_text = fields.TextField(default="")
    error_message = fields.TextField(default="")
    result_summary = fields.TextField(default="")
    created_at = fields.DatetimeField(auto_now_add=True, db_index=True)

    class Meta:
        table = "log_analysis_llm_calls"
        ordering = ["created_at", "id"]


class SitemapAnalysis(DatabaseModel):
    """Stored sitemap-analysis report and execution state."""

    class Severity(StrEnum):
        INFO = "INFO"
        WARNING = "WARNING"
        CRITICAL = "CRITICAL"

    objects: ClassVar[SitemapAnalysisManager] = SitemapAnalysisManager()

    id = fields.IntField(
        primary_key=True,
        description="Database-generated integer id for this sitemap analysis.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        db_index=True,
        description="UTC timestamp when this sitemap analysis row was created.",
    )
    analysis_date = fields.DateField(
        unique=True,
        db_index=True,
        description="Calendar date this sitemap analysis represents.",
    )

    status = fields.CharField(
        max_length=20,
        default=RunStatus.PENDING.value,
        db_index=True,
        description="Execution status for this sitemap analysis job.",
    )
    started_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp when this sitemap analysis job started.",
    )
    finished_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp when this sitemap analysis job finished.",
    )
    failure_stage = fields.CharField(
        max_length=80,
        null=True,
        description="Pipeline stage where this sitemap analysis failed, if any.",
    )
    fetch_duration_seconds = fields.FloatField(
        default=0.0,
        description="Total time spent fetching and parsing sitemap data.",
    )

    root_sitemap_url = fields.CharField(
        max_length=2048,
        description="Root sitemap URL inspected by the sitemap analysis job.",
    )
    total_sitemaps = fields.IntField(
        default=0,
        description="Number of sitemap files discovered during analysis.",
    )
    total_urls = fields.IntField(
        default=0,
        description="Number of URLs discovered across all sitemap files.",
    )
    issue_summary: fields.JSONField[dict[str, int]] = fields.JSONField(
        default=dict,
        description="Structured summary of sitemap issues by category.",
    )
    issues: fields.JSONField[list[dict[str, Any]]] = fields.JSONField(
        default=list,
        description="Structured list of sitemap issues found by deterministic checks.",
    )

    summary = fields.TextField(description="LLM-generated sitemap analysis summary.")
    severity = fields.CharField(
        max_length=10,
        default=Severity.INFO.value,
        db_index=True,
        description="LLM-classified severity for this sitemap analysis.",
    )
    key_findings: fields.JSONField[list[str]] = fields.JSONField(
        default=list,
        description="List of important findings extracted from the sitemap analysis.",
    )
    recommendations = fields.TextField(
        default="",
        description="LLM-generated sitemap recommendations.",
    )
    trend_summary = fields.TextField(
        default="",
        description="LLM-generated trend comparison against prior sitemap analyses.",
    )

    execution_time_seconds = fields.FloatField(
        default=0.0,
        description="Total wall-clock execution time for this sitemap analysis job.",
    )
    gpt_tokens_used = fields.IntField(
        default=0,
        description="OpenAI token count used for this sitemap analysis.",
    )
    gpt_cost_usd = fields.FloatField(
        default=0.0,
        description="Estimated OpenAI API cost in USD for this sitemap analysis.",
    )
    email_sent = fields.BooleanField(
        default=False,
        db_index=True,
        description="Whether the sitemap analysis email was sent.",
    )
    error_message = fields.TextField(
        default="",
        description="Error message captured when this sitemap analysis failed.",
    )

    class Meta:
        table = "sitemap_analyses"
        ordering = ["-analysis_date"]

    def __str__(self) -> str:
        return f"Sitemap Analysis {self.analysis_date} ({self.severity})"

    @property
    def execution_time_formatted(self) -> str:
        """Return execution time formatted for reports."""

        return f"{self.execution_time_seconds:.1f}"

    @property
    def issue_count(self) -> int:
        """Return the number of deterministic sitemap issues."""

        return len(self.issues)

    @property
    def issue_summary_lines(self) -> list[str]:
        """Return issue summary lines formatted for reports."""

        lines: list[str] = []
        for category, count in sorted(self.issue_summary.items()):
            lines.append(f"{category.replace('_', ' ')}: {count}")
        return lines

    async def mark_email_sent(self) -> None:
        """Mark this sitemap analysis email as sent."""

        self.email_sent = True
        await self.save(update_fields=["email_sent"])
