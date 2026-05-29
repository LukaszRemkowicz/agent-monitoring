from datetime import UTC, datetime

from schemas import CollectLogsArtifact, LogAnalysisFinalReport, LogAnalysisToolResult
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
        tool_name="group_errors",
        arguments={"project_name": "landingpage"},
        structured_content={
            "action": "group_errors",
            "project_name": "landingpage",
            "groups": [{"message": "No repeated errors detected", "count": 0}],
        },
    )
    final_report = LogAnalysisFinalReport(
        action="final_report",
        summary="Logs are healthy with routine scanner noise.",
        severity="INFO",
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
    assert packet.coverage_snapshot["totals"] == {
        "projects": 1,
        "sources": 2,
        "collected_sources": 1,
        "unavailable_sources": 1,
        "zero_line_sources": 1,
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
    }
    assert packet.deterministic_fingerprint["report"] == {
        "severity": "INFO",
        "key_finding_count": 1,
        "evidence_count": 1,
        "coverage_gap_count": 1,
        "watch_only_count": 1,
    }
    assert packet.deterministic_fingerprint["tool_results"] == [
        {
            "tool_name": "group_errors",
            "arguments_hash": hash_text(dump_arguments({"project_name": "landingpage"})),
            "action": "group_errors",
            "result_hash": hash_text(dump_arguments(tool_result.structured_content)),
        }
    ]
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
