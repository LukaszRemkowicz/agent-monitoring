from __future__ import annotations

import re
import smtplib
from collections.abc import Iterable
from datetime import date, datetime
from email.message import EmailMessage
from html import unescape
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from conf import settings
from logging_config import get_logger
from schemas import LogAnalysisOut, SitemapAnalysisOut

logger = get_logger(__name__)


class MonitoringEmailSender(Protocol):
    async def send_log_analysis(self, analysis: LogAnalysisOut) -> None: ...

    async def send_sitemap_analysis(self, analysis: SitemapAnalysisOut) -> None: ...


class MonitoringTemplateRenderer(Protocol):
    def render(self, template_name: str, context: dict[str, Any]) -> str: ...


class MonitoringEmailConfig(BaseModel):
    """Configuration needed to render and send monitoring emails."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    template_root: Path
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool
    from_email: EmailStr
    log_recipients: list[EmailStr] = Field(min_length=1)
    sitemap_recipients: list[EmailStr] = Field(min_length=1)
    admin_domain: str
    environment: str
    monitoring_project: str

    @classmethod
    def from_settings(cls) -> MonitoringEmailConfig:
        log_recipients: list[str] = cls._parse_recipients(settings.EMAIL_TO)
        sitemap_recipients: list[str] = (
            cls._parse_recipients(settings.SITEMAP_EMAIL_TO) or log_recipients
        )
        return cls.model_validate(
            {
                "template_root": settings.TEMPLATE_ROOT,
                "smtp_host": settings.EMAIL_HOST,
                "smtp_port": settings.EMAIL_PORT,
                "smtp_username": settings.EMAIL_USERNAME,
                "smtp_password": settings.EMAIL_PASSWORD,
                "smtp_use_tls": settings.EMAIL_USE_TLS,
                "from_email": settings.EMAIL_FROM,
                "log_recipients": log_recipients,
                "sitemap_recipients": sitemap_recipients,
                "admin_domain": settings.ADMIN_DOMAIN,
                "environment": settings.ENVIRONMENT,
                "monitoring_project": settings.MONITORING_PROJECT,
            }
        )

    @staticmethod
    def _parse_recipients(value: str | Iterable[str]) -> list[str]:
        if isinstance(value, str):
            raw_values = value.replace(";", ",").split(",")
        else:
            raw_values = list(value)
        return [recipient.strip() for recipient in raw_values if recipient.strip()]


class MonitoringEmailRenderer:
    """Render monitoring email templates with Jinja."""

    def __init__(self, *, template_root: Path) -> None:
        self.environment = Environment(
            loader=FileSystemLoader(str(template_root)),
            autoescape=select_autoescape(("html", "xml")),
        )

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        """Render one monitoring email template."""

        template = self.environment.get_template(template_name)
        return template.render(**context)


class MonitoringEmailService:
    """Compose and send monitoring report emails."""

    LOG_ANALYSIS_TEMPLATE = "monitoring/log_analysis.html"
    SITEMAP_ANALYSIS_TEMPLATE = "monitoring/sitemap_analysis.html"

    def __init__(self, config: MonitoringEmailConfig, renderer: MonitoringTemplateRenderer) -> None:
        self.config = config
        self.renderer = renderer

    @classmethod
    def create_default(cls) -> MonitoringEmailService:
        """Create the command-layer email service from application settings."""

        config = MonitoringEmailConfig.from_settings()
        return cls(
            config=config,
            renderer=MonitoringEmailRenderer(template_root=config.template_root),
        )

    async def send_log_analysis(self, analysis: LogAnalysisOut) -> None:
        self.send(
            subject=self._log_analysis_subject(analysis),
            template_name=self.LOG_ANALYSIS_TEMPLATE,
            context=self._log_analysis_context(analysis),
            recipients=self.config.log_recipients,
        )

    async def send_sitemap_analysis(self, analysis: SitemapAnalysisOut) -> None:
        self.send(
            subject=self._sitemap_analysis_subject(analysis),
            template_name=self.SITEMAP_ANALYSIS_TEMPLATE,
            context=self._sitemap_analysis_context(analysis),
            recipients=self.config.sitemap_recipients,
        )

    def send(
        self,
        subject: str,
        template_name: str,
        context: dict[str, Any],
        recipients: list[EmailStr],
    ) -> None:
        """Render a Jinja template and send it as an HTML email."""

        html_content = self.renderer.render(template_name, context)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = str(self.config.from_email)
        message["To"] = ", ".join(str(recipient) for recipient in recipients)
        message.set_content(self._html_to_text(html_content))
        message.add_alternative(html_content, subtype="html")

        logger.info(
            "sending monitoring email",
            extra={
                "event": "monitoring_email_send_start",
                "recipient_count": len(recipients),
                "subject": subject,
            },
        )
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=30) as smtp:
            if self.config.smtp_use_tls:
                smtp.starttls()
            if self.config.smtp_username:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
            smtp.send_message(message)
        logger.info(
            "sent monitoring email",
            extra={
                "event": "monitoring_email_send_done",
                "recipient_count": len(recipients),
                "subject": subject,
            },
        )

    def _log_analysis_context(self, analysis: LogAnalysisOut) -> dict[str, Any]:
        return {
            "environment": self.config.environment,
            "monitoring_project": self.config.monitoring_project,
            "log_analysis": analysis,
            "analysis_date": self._format_email_date(analysis.analysis_date).upper(),
            "log_size": analysis.log_size,
            "execution_time": f"{analysis.execution_time_seconds:.1f}",
            "admin_domain": self.config.admin_domain,
            "current_year": datetime.now().year,
        }

    def _sitemap_analysis_context(self, analysis: SitemapAnalysisOut) -> dict[str, Any]:
        return {
            "environment": self.config.environment,
            "monitoring_project": self.config.monitoring_project,
            "sitemap_analysis": analysis,
            "analysis_date": self._format_email_date(analysis.analysis_date).upper(),
            "execution_time": f"{analysis.execution_time_seconds:.1f}",
            "admin_domain": self.config.admin_domain,
            "current_year": datetime.now().year,
        }

    def _log_analysis_subject(self, analysis: LogAnalysisOut) -> str:
        environment = self.config.environment.upper()
        return f"[{environment}][{analysis.severity}] Daily Log Analysis - {analysis.analysis_date}"

    def _sitemap_analysis_subject(self, analysis: SitemapAnalysisOut) -> str:
        environment = self.config.environment.upper()
        return f"[{environment}][{analysis.severity}] Sitemap Analysis - {analysis.analysis_date}"

    @staticmethod
    def _format_email_date(value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return value.strftime("%B %d, %Y")
        if isinstance(value, date):
            return value.strftime("%B %d, %Y")
        return str(value)

    @staticmethod
    def _html_to_text(html_content: str) -> str:
        without_tags = re.sub(r"<[^>]+>", " ", html_content)
        collapsed = re.sub(r"\s+", " ", unescape(without_tags)).strip()
        return collapsed or "Monitoring report attached as HTML."
