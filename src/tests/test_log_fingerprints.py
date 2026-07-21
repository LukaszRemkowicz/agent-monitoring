from datetime import UTC, datetime

from pydantic import ValidationError

from schemas import (
    CollectLogsArtifact,
    LogAnalysisFinalReport,
    LogAnalysisFingerprints,
    LogAnalysisIn,
    LogAnalysisSeverity,
    LogAnalysisToolResult,
    McpToolName,
)
from services.log_fingerprints import (
    LOG_ANALYSIS_FINGERPRINT_VERSION,
    LogAnalysisFingerprintBuilder,
)
from tests.conftest import build_collect_logs_artifact_payload
from utils.runtime import dump_arguments, hash_text


def test_log_analysis_fingerprint_builder_summarizes_current_run_facts() -> None:
    collect_logs = CollectLogsArtifact.model_validate(
        build_collect_logs_artifact_payload(include_unavailable_nginx=True)
    )
    tool_result = LogAnalysisToolResult(
        tool_name=McpToolName.GROUP_ERRORS,
        arguments={"project_name": "demo-shop"},
        structured_content={
            "action": McpToolName.GROUP_ERRORS,
            "analysis_cautions": ["Only grouped matching lines are shown."],
            "grouped_error_count": 1,
            "project_name": "demo-shop",
            "requested_project_name": "demo-shop",
            "searched_source_keys": ["nginx"],
            "matching_line_count": 5,
            "max_groups": 50,
            "next_step_tips": ["Use inspect_proxy_activity for HTTP status totals."],
            "session_id": "session-123",
            "snapshot_dir": "sessions/test/demo-shop",
            "summary": "Found one grouped scanner probe.",
            "truncated": False,
            "workspace": "workflow",
            "groups": [
                {
                    "fingerprint": "nginx:http_4xx:404:/.env",
                    "category": "http_4xx",
                    "severity": "medium",
                    "count": 5,
                    "source_keys": ["nginx"],
                    "request_paths": ["/.env"],
                    "status_codes": [404],
                    "levels": [],
                    "message_summary": "404 on /.env scanner probe",
                    "first_timestamp": "2026-05-19T02:00:00Z",
                    "last_timestamp": "2026-05-19T03:00:00Z",
                    "first_seen": {
                        "line": "first raw line",
                        "line_number": 7,
                        "line_truncated": False,
                        "output_file": "sessions/test/demo-shop/nginx.log",
                        "source_key": "nginx",
                    },
                    "last_seen": {
                        "line": "last raw line",
                        "line_number": 9,
                        "line_truncated": True,
                        "output_file": "sessions/test/demo-shop/nginx.log",
                        "source_key": "nginx",
                    },
                }
            ],
        },
    )
    final_report = LogAnalysisFinalReport(
        action="final_report",
        summary="Logs are healthy with routine scanner noise.",
        severity=LogAnalysisSeverity.INFO,
        severity_rationale="No service-impacting issue was found.",
        key_findings=["No critical incidents found."],
        evidence=["group_errors found no repeated errors."],
        coverage_gaps=["nginx access source unavailable"],
        recommendations="Keep watching scanner noise.",
        watch_only_items=["Routine bot traffic."],
        trend_summary="No prior trend data was available.",
    )

    packet = LogAnalysisFingerprintBuilder.build(
        collect_logs=collect_logs,
        tool_results=[tool_result],
        final_report=final_report,
        log_window_since=datetime(2026, 5, 19, tzinfo=UTC),
        log_window_until=datetime(2026, 5, 20, tzinfo=UTC),
    )

    assert packet.fingerprint_version == LOG_ANALYSIS_FINGERPRINT_VERSION
    assert isinstance(packet.fingerprints, LogAnalysisFingerprints)
    assert packet.coverage_snapshot["totals"] == {
        "projects": 1,
        "sources": 2,
        "collected_sources": 1,
        "unavailable_sources": 1,
        "zero_line_sources": 1,
        "truncated_sources": 0,
    }
    assert packet.coverage_snapshot["projects"][0]["sources"][1] == {
        "source_key": "nginx",
        "source_type": "file",
        "status": "unavailable",
        "line_count": 0,
        "byte_count": 0,
        "zero_lines": True,
        "has_output_file": False,
        "error": "file missing",
        "truncated": False,
        "continuation_available": False,
    }
    assert packet.fingerprints.report.severity == "INFO"
    assert packet.fingerprints.report.key_finding_count == 1
    assert packet.fingerprints.report.evidence_count == 1
    assert packet.fingerprints.report.coverage_gap_count == 1
    assert packet.fingerprints.report.watch_only_count == 1
    assert packet.fingerprints.tool_results[0].tool_name == McpToolName.GROUP_ERRORS
    assert packet.fingerprints.tool_results[0].arguments_hash == hash_text(
        dump_arguments({"project_name": "demo-shop"})
    )
    assert packet.fingerprints.tool_results[0].action == McpToolName.GROUP_ERRORS
    assert packet.fingerprints.tool_results[0].result_hash == hash_text(
        dump_arguments(tool_result.structured_content)
    )
    grouped_errors = packet.fingerprints.grouped_error_runs[0].result.groups
    assert grouped_errors[0].fingerprint == ("nginx:http_4xx:404:/.env")
    assert grouped_errors[0].count == 5
    assert grouped_errors[0].first_seen is not None
    assert grouped_errors[0].first_seen.line == "first raw line"
    assert grouped_errors[0].first_seen.line_number == 7
    assert grouped_errors[0].first_seen.line_truncated is False
    assert grouped_errors[0].first_seen.output_file == "sessions/test/demo-shop/nginx.log"
    assert grouped_errors[0].first_seen.source_key == "nginx"
    assert grouped_errors[0].last_seen is not None
    assert grouped_errors[0].last_seen.line == "last raw line"
    assert grouped_errors[0].last_seen.line_number == 9
    assert grouped_errors[0].last_seen.line_truncated is True
    assert packet.fingerprints.grouped_error_runs[0].arguments == {"project_name": "demo-shop"}
    assert packet.fingerprints.grouped_error_runs[0].result.action == McpToolName.GROUP_ERRORS
    assert packet.fingerprints.grouped_error_runs[0].result.analysis_cautions == [
        "Only grouped matching lines are shown."
    ]
    assert packet.fingerprints.grouped_error_runs[0].result.grouped_error_count == 1
    assert packet.fingerprints.grouped_error_runs[0].result.matching_line_count == 5
    assert packet.fingerprints.grouped_error_runs[0].result.max_groups == 50
    assert packet.fingerprints.grouped_error_runs[0].result.next_step_tips == [
        "Use inspect_proxy_activity for HTTP status totals."
    ]
    assert packet.fingerprints.grouped_error_runs[0].result.project_name == "demo-shop"
    assert packet.fingerprints.grouped_error_runs[0].result.requested_project_name == "demo-shop"
    assert packet.fingerprints.grouped_error_runs[0].result.searched_source_keys == ["nginx"]
    assert packet.fingerprints.grouped_error_runs[0].result.session_id == "session-123"
    assert packet.fingerprints.grouped_error_runs[0].result.snapshot_dir == (
        "sessions/test/demo-shop"
    )
    assert packet.fingerprints.grouped_error_runs[0].result.summary == (
        "Found one grouped scanner probe."
    )
    assert packet.fingerprints.grouped_error_runs[0].result.truncated is False
    assert packet.fingerprints.grouped_error_runs[0].result.workspace == "workflow"
    assert "grouped_error_signals" not in packet.fingerprints.model_dump()
    assert packet.evidence_fingerprints == [
        "evidence:" + hash_text("group_errors found no repeated errors."),
        "tool:group_errors:" + hash_text(dump_arguments(tool_result.structured_content)),
    ]
    assert packet.known_patterns == [
        {
            "source": "final_report.watch_only_items",
            "pattern": "Routine bot traffic.",
        }
    ]


def test_coverage_snapshot_preserves_transfer_completeness() -> None:
    payload = build_collect_logs_artifact_payload()
    payload["projects"][0]["sources"][0]["transfer"] = {
        "encoding": "base64",
        "operation": "container_logs_page",
        "truncated": True,
        "byte_limit": 1_000_000,
        "page_count": 1,
        "next_offset": 1_000_000,
        "returned_bytes": 1_000_000,
    }
    artifact = CollectLogsArtifact.model_validate(payload)

    snapshot = LogAnalysisFingerprintBuilder.build_coverage_snapshot(artifact)

    assert snapshot["projects"][0]["sources"][0]["truncated"] is True
    assert snapshot["projects"][0]["sources"][0]["continuation_available"] is True
    assert snapshot["totals"]["truncated_sources"] == 1


def test_log_analysis_rejects_unknown_fingerprint_shape() -> None:
    try:
        LogAnalysisIn(
            analysis_date=datetime(2026, 5, 19, tzinfo=UTC).date(),
            status="succeeded",
            summary="Done.",
            fingerprints={"status_totals": {"404": 12}},  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        assert "status_totals" in str(exc)
    else:
        raise AssertionError("LogAnalysisIn accepted unknown fingerprint fields.")
