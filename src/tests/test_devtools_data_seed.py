from datetime import date
from typing import Any, cast

from db.models import LogAnalysis
from devtools import data_seed


def test_manual_fixture_seed_fingerprints_use_sanitized_fixture_names() -> None:
    analysis_date = date(2026, 5, 18)

    baseline = data_seed._baseline_fingerprints(
        analysis_date=analysis_date,
        severity=LogAnalysis.Severity.INFO,
    )
    watch = data_seed._watch_only_fingerprints(
        analysis_date=analysis_date,
        severity=LogAnalysis.Severity.WARNING,
    )

    assert baseline.collection.requested_project_names == ["demo-shop", "host-security"]
    assert baseline.grouped_error_runs[0].arguments == {"project_name": "demo-shop"}
    assert [group.fingerprint for group in baseline.grouped_error_runs[0].result.groups] == [
        "demo-shop:edge:http_403:/.git/config",
        "demo-shop:edge:http_404:scanner-wordpress",
    ]
    assert [group.fingerprint for group in watch.grouped_error_runs[0].result.groups] == [
        "demo-shop:backend:error:celery:catalog-metadata-sync-timeout"
    ]


def test_manual_fixture_seed_coverage_uses_sanitized_fixture_names() -> None:
    snapshot = data_seed._coverage_snapshot(date(2026, 5, 18))
    projects = cast(list[dict[str, Any]], snapshot["projects"])
    demo_shop_sources = cast(list[dict[str, Any]], projects[0]["sources"])
    host_security_sources = cast(list[dict[str, Any]], projects[1]["sources"])

    assert projects[0]["project_name"] == "demo-shop"
    assert projects[0]["snapshot_dir"] == "workflow/demo-shop/2026-05-18"
    assert [source["source_key"] for source in demo_shop_sources] == [
        "nginx",
        "traefik",
        "backend",
        "frontend",
        "worker",
        "scheduler",
    ]
    assert projects[1]["project_name"] == "host-security"
    assert projects[1]["snapshot_dir"] == "workflow/host-security/2026-05-18"
    assert [source["source_key"] for source in host_security_sources] == [
        "fail2ban",
        "edge_access",
        "proxy_access",
    ]
