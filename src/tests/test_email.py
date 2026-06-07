from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture

from schemas import LogAnalysisOut, SitemapAnalysisOut
from services.email import (
    MonitoringEmailConfig,
    MonitoringEmailRenderer,
    MonitoringEmailService,
    MonitoringFailureEmail,
)
from tests.conftest import build_collect_logs_artifact_payload, override_settings

LOCAL_TEMPLATE_ROOT = Path("src/templates/monitoring")


def _email_config(
    *,
    log_recipients: str = "ops@example.com",
    sitemap_recipients: str = "",
    admin_domain: str = "admin.example.com",
    smtp_host: str = "smtp.example.com",
    smtp_port: int = 2525,
    smtp_username: str = "",
    smtp_password: str = "",
    smtp_use_tls: bool = False,
    from_email: str = "monitoring@example.com",
) -> MonitoringEmailConfig:
    parsed_log_recipients = _parse_test_recipients(log_recipients)
    return MonitoringEmailConfig(
        template_root=Path("src/templates"),
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_use_tls=smtp_use_tls,
        from_email=from_email,
        log_recipients=parsed_log_recipients,
        sitemap_recipients=_parse_test_recipients(sitemap_recipients) or parsed_log_recipients,
        admin_domain=admin_domain,
        environment="dev",
        monitoring_project="demo-shop",
    )


def _parse_test_recipients(value: str) -> list[str]:
    return [recipient.strip() for recipient in value.split(",") if recipient.strip()]


def _email_service(config: MonitoringEmailConfig) -> MonitoringEmailService:
    return MonitoringEmailService(
        config=config,
        renderer=MonitoringEmailRenderer(template_root=config.template_root),
    )


def test_monitoring_email_config_requires_recipients() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _email_config(log_recipients="")

    assert {error["loc"] for error in exc_info.value.errors()} == {
        ("log_recipients",),
        ("sitemap_recipients",),
    }


def test_monitoring_email_templates_use_jinja_syntax() -> None:
    django_template_tokens = (
        '|date:"',
        "|floatformat:",
        '|default:"',
        "{% empty %}",
        "{% now ",
        ".splitlines %}",
        ".strip %}",
    )
    for template_name in ("log_analysis.html", "sitemap_analysis.html", "failure.html"):
        template_text = (LOCAL_TEMPLATE_ROOT / template_name).read_text()

        assert not any(token in template_text for token in django_template_tokens)


def test_monitoring_failure_email_renders_error_context() -> None:
    failure = MonitoringFailureEmail(
        command_name="log_analysis",
        analysis_date=date(2026, 5, 19),
        error_type="RuntimeError",
        error_message="MCP workflow unavailable",
        traceback_text="Traceback (most recent call last):\nRuntimeError: MCP workflow unavailable",
    )
    service = _email_service(_email_config())

    html = service.renderer.render(
        "monitoring/failure.html",
        service._failure_context(failure),
    )

    assert "Portfolio - Monitoring Failure" in html
    assert "log_analysis" in html
    assert "MCP workflow unavailable" in html
    assert "RuntimeError" in html
    assert "Traceback (most recent call last):" in html


def test_log_analysis_email_renders_copied_template() -> None:
    analysis = LogAnalysisOut(
        id=7,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=date(2026, 5, 19),
        status="SUCCEEDED",
        summary="Demo shop logs are healthy.",
        severity="INFO",
        key_findings=["No critical incidents found."],
        recommendations="Keep watching the backend logs.",
        trend_summary="Scanner noise is stable.",
        execution_time_seconds=12.34,
        gpt_tokens_used=123,
        gpt_cost_usd=0.012345,
    )
    with override_settings(
        ENVIRONMENT="dev",
        ADMIN_DOMAIN="admin.example.com",
        EMAIL_TO="ops@example.com",
    ):
        service = MonitoringEmailService.create_default()

    html = service.renderer.render(
        "monitoring/log_analysis.html",
        {
            "environment": "dev",
            "monitoring_project": "demo-shop",
            "log_analysis": analysis,
            "analysis_date": "MAY 19, 2026",
            "log_size": "0.0 KB",
            "execution_time": "12.3",
            "admin_domain": "admin.example.com",
            "current_year": 2026,
        },
    )

    assert "Portfolio - Daily Log Analysis" in html
    assert "Demo shop logs are healthy." in html
    assert "No critical incidents found." in html
    assert "https://admin.example.com/admin/monitoring/loganalysis/7/" in html


def test_log_analysis_email_omits_admin_link_without_admin_domain() -> None:
    analysis = LogAnalysisOut(
        id=7,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=date(2026, 5, 19),
        status="SUCCEEDED",
        summary="Demo shop logs are healthy.",
        severity="INFO",
        key_findings=["No critical incidents found."],
    )
    service = _email_service(_email_config(admin_domain=""))

    html = service.renderer.render(
        "monitoring/log_analysis.html",
        {
            "environment": "dev",
            "monitoring_project": "demo-shop",
            "log_analysis": analysis,
            "analysis_date": "MAY 19, 2026",
            "log_size": "0.0 KB",
            "execution_time": "0.0",
            "admin_domain": "",
            "current_year": 2026,
        },
    )

    assert "View Full Report" not in html
    assert "/admin/monitoring/loganalysis/" not in html


def test_log_analysis_email_context_uses_mcp_artifact_size() -> None:
    analysis = LogAnalysisOut(
        id=7,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=date(2026, 5, 19),
        mcp_artifact={"collect_logs": build_collect_logs_artifact_payload()},
        status="SUCCEEDED",
        summary="Demo shop logs are healthy.",
        severity="INFO",
        key_findings=[],
    )
    service = _email_service(_email_config())

    assert service._log_analysis_context(analysis)["log_size"] == "4.0 KB"


def test_log_analysis_email_context_uses_mb_for_large_mcp_artifacts() -> None:
    artifact = build_collect_logs_artifact_payload()
    artifact["projects"][0]["sources"][0]["byte_count"] = 5 * 1024 * 1024
    analysis = LogAnalysisOut(
        id=7,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=date(2026, 5, 19),
        mcp_artifact={"collect_logs": artifact},
        status="SUCCEEDED",
        summary="Demo shop logs are healthy.",
        severity="INFO",
        key_findings=[],
    )
    service = _email_service(_email_config())

    assert service._log_analysis_context(analysis)["log_size"] == "5.0 MB"


def test_sitemap_analysis_email_renders_copied_template() -> None:
    analysis = SitemapAnalysisOut(
        id=9,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=date(2026, 5, 19),
        status="SUCCEEDED",
        root_sitemap_url="https://example.com/sitemap.xml",
        total_sitemaps=1,
        total_urls=2,
        summary="Sitemap is healthy.",
        severity="INFO",
        key_findings=["All sitemap URLs are valid."],
        recommendations="No action needed.",
        trend_summary="No change from prior run.",
        execution_time_seconds=4.56,
        gpt_tokens_used=10,
        gpt_cost_usd=0.0025,
    )
    with override_settings(
        ENVIRONMENT="dev",
        ADMIN_DOMAIN="admin.example.com",
        EMAIL_TO="ops@example.com",
    ):
        service = MonitoringEmailService.create_default()

    html = service.renderer.render(
        "monitoring/sitemap_analysis.html",
        {
            "environment": "dev",
            "monitoring_project": "demo-shop",
            "sitemap_analysis": analysis,
            "analysis_date": "MAY 19, 2026",
            "execution_time": "4.6",
            "admin_domain": "admin.example.com",
            "current_year": 2026,
        },
    )

    assert "Portfolio - Sitemap Analysis" in html
    assert "Sitemap is healthy." in html
    assert "All sitemap URLs are valid." in html
    assert "https://admin.example.com/admin/monitoring/sitemapanalysis/9/change/" in html


def test_sitemap_analysis_email_omits_admin_link_without_admin_domain() -> None:
    analysis = SitemapAnalysisOut(
        id=9,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        analysis_date=date(2026, 5, 19),
        status="SUCCEEDED",
        root_sitemap_url="https://example.com/sitemap.xml",
        summary="Sitemap is healthy.",
        severity="INFO",
    )
    service = _email_service(_email_config(admin_domain=""))

    html = service.renderer.render(
        "monitoring/sitemap_analysis.html",
        {
            "environment": "dev",
            "monitoring_project": "demo-shop",
            "sitemap_analysis": analysis,
            "analysis_date": "MAY 19, 2026",
            "execution_time": "0.0",
            "admin_domain": "",
            "current_year": 2026,
        },
    )

    assert "View Full Report" not in html
    assert "/admin/monitoring/sitemapanalysis/" not in html


def test_monitoring_email_service_sends_html_email(mocker: MockerFixture) -> None:
    smtp = mocker.patch("services.email.smtplib.SMTP").return_value.__enter__.return_value
    service = _email_service(
        _email_config(
            smtp_host="smtp.example.com",
            smtp_port=2525,
            smtp_username="user",
            smtp_password="password",
            smtp_use_tls=True,
            from_email="monitoring@example.com",
            log_recipients="ops@example.com,admin@example.com",
        )
    )

    service.send(
        subject="Monitoring report",
        template_name="monitoring/log_analysis.html",
        context={
            "environment": "dev",
            "monitoring_project": "demo-shop",
            "log_analysis": LogAnalysisOut(
                id=7,
                created_at=datetime(2026, 5, 19, tzinfo=UTC),
                analysis_date=date(2026, 5, 19),
                status="SUCCEEDED",
                summary="Report",
                severity="INFO",
                key_findings=[],
            ),
            "analysis_date": "MAY 19, 2026",
            "log_size": "0.0 KB",
            "execution_time": "0.0",
            "admin_domain": "",
            "current_year": 2026,
        },
        recipients=service.config.log_recipients,
    )

    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("user", "password")
    sent_message = smtp.send_message.call_args.args[0]
    assert sent_message["Subject"] == "Monitoring report"
    assert sent_message["From"] == "monitoring@example.com"
    assert sent_message["To"] == "ops@example.com, admin@example.com"
