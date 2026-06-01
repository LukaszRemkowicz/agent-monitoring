from __future__ import annotations

from datetime import datetime
from typing import Any

from schemas import (
    CollectLogsArtifact,
    LogAnalysisFinalReport,
    LogAnalysisFingerprintPacket,
    LogAnalysisToolResult,
)
from utils.runtime import dump_arguments, hash_text

LOG_ANALYSIS_FINGERPRINT_VERSION = "log-analysis-fingerprint-v1"


class LogAnalysisFingerprintBuilder:
    """Build compact current-run comparison data from deterministic facts."""

    @staticmethod
    def build(
        *,
        collect_logs: CollectLogsArtifact,
        tool_results: list[LogAnalysisToolResult],
        final_report: LogAnalysisFinalReport,
        log_window_since: datetime,
        log_window_until: datetime,
    ) -> LogAnalysisFingerprintPacket:
        coverage_snapshot: dict[str, Any] = LogAnalysisFingerprintBuilder.build_coverage_snapshot(
            collect_logs
        )
        tool_fingerprints: list[dict[str, str]] = _build_tool_fingerprints(tool_results)
        return LogAnalysisFingerprintPacket(
            fingerprint_version=LOG_ANALYSIS_FINGERPRINT_VERSION,
            deterministic_fingerprint={
                "version": LOG_ANALYSIS_FINGERPRINT_VERSION,
                "log_window": {
                    "since": log_window_since.isoformat(),
                    "until": log_window_until.isoformat(),
                },
                "collection": {
                    "workspace": collect_logs.workspace,
                    "requested_project_names": collect_logs.requested_project_names,
                    "project_count": len(collect_logs.projects),
                },
                "coverage_totals": coverage_snapshot["totals"],
                "tool_results": tool_fingerprints,
                "report": {
                    "severity": final_report.severity,
                    "key_finding_count": len(final_report.key_findings),
                    "evidence_count": len(final_report.evidence),
                    "coverage_gap_count": len(final_report.coverage_gaps),
                    "watch_only_count": len(final_report.watch_only_items),
                },
            },
            evidence_fingerprints=[
                f"evidence:{hash_text(evidence)}" for evidence in final_report.evidence
            ]
            + [
                f"tool:{tool_result.tool_name}:{_hash_mapping(tool_result.structured_content)}"
                for tool_result in tool_results
            ],
            known_patterns=[
                {
                    "source": "final_report.watch_only_items",
                    "pattern": item,
                }
                for item in final_report.watch_only_items
            ],
            coverage_snapshot=coverage_snapshot,
        )

    @staticmethod
    def build_coverage_snapshot(collect_logs: CollectLogsArtifact) -> dict[str, Any]:
        """Summarize which log sources were observable without needing a final report.

        This snapshot is the deterministic coverage baseline for later comparison.
        It records project/source status, line counts, zero-line observations, and
        unavailable-source errors without copying raw log lines into Postgres. A
        future run can compare this object with its own snapshot before the LLM
        decides whether a source coverage change is meaningful.

        The service also uses this method when the workflow fails after
        `collect_logs`, before any final report exists. That lets failed runs
        still persist useful coverage facts for debugging and for the next run's
        history comparison.
        """

        projects: list[dict[str, Any]] = []
        source_count = 0
        collected_source_count = 0
        unavailable_source_count = 0
        zero_line_source_count = 0

        for project in collect_logs.projects:
            sources: list[dict[str, Any]] = []
            for source in project.sources:
                source_count += 1
                if source.status == "collected":
                    collected_source_count += 1
                if source.status == "unavailable":
                    unavailable_source_count += 1
                if source.line_count == 0:
                    zero_line_source_count += 1
                sources.append(
                    {
                        "source_key": source.source_key,
                        "source_type": source.source_type,
                        "status": source.status,
                        "line_count": source.line_count,
                        "byte_count": source.byte_count,
                        "zero_lines": source.line_count == 0,
                        "has_output_file": bool(source.output_file),
                        "error": source.error,
                    }
                )
            projects.append(
                {
                    "project_name": project.project_name,
                    "snapshot_dir": project.snapshot_dir,
                    "warnings": project.warnings,
                    "sources": sources,
                }
            )

        return {
            "projects": projects,
            "totals": {
                "projects": len(collect_logs.projects),
                "sources": source_count,
                "collected_sources": collected_source_count,
                "unavailable_sources": unavailable_source_count,
                "zero_line_sources": zero_line_source_count,
            },
        }


def _build_tool_fingerprints(
    tool_results: list[LogAnalysisToolResult],
) -> list[dict[str, str]]:
    return [
        {
            "tool_name": tool_result.tool_name,
            "arguments_hash": hash_text(dump_arguments(tool_result.arguments)),
            "action": str(tool_result.structured_content.get("action", "")),
            "result_hash": _hash_mapping(tool_result.structured_content),
        }
        for tool_result in tool_results
    ]


def _hash_mapping(value: dict[str, Any]) -> str:
    return hash_text(dump_arguments(value))
