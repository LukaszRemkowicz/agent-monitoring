from datetime import UTC, date, datetime

import pytest

from exceptions import LogAnalysisComparisonMissingException
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAllowedAction,
    LogAnalysisCurrentCoverage,
    LogAnalysisEvidenceMode,
    LogAnalysisFinalReport,
    LogAnalysisFingerprintArgumentValue,
    LogAnalysisFingerprints,
    LogAnalysisGroupedErrorComparison,
    LogAnalysisGroupedErrorRunFingerprint,
    LogAnalysisGroupedErrorSignal,
    LogAnalysisGroupedErrorsResult,
    LogAnalysisNextRequiredAction,
    LogAnalysisOut,
    LogAnalysisPromptCollection,
    LogAnalysisPromptContext,
    LogAnalysisPromptPhase,
    LogAnalysisSeverity,
    LogWorkspace,
    McpToolName,
    PreviousLogAnalysisContext,
    RecommendedAction,
    SnapshotAccessGuidance,
)
from services.log_fingerprints import LogAnalysisFingerprintBuilder
from services.log_history_comparison import LogAnalysisHistoryComparisonService
from tests.conftest import build_collect_logs_artifact_payload


def test_prompt_grouped_error_compaction_requires_comparison() -> None:
    with pytest.raises(
        LogAnalysisComparisonMissingException,
        match="grouped-error comparison is required for prompt compaction",
    ):
        LogAnalysisHistoryComparisonService.compact_grouped_error_comparison_for_prompt(None)  # type: ignore[arg-type]


def _grouped_error_run(
    *,
    project_name: str = "landingpage",
    source_keys: list[str] | None = None,
    groups: list[dict[str, object]] | None = None,
) -> LogAnalysisGroupedErrorRunFingerprint:
    arguments: dict[str, LogAnalysisFingerprintArgumentValue] = {"project_name": project_name}
    if source_keys is not None:
        arguments["source_keys"] = source_keys
    return LogAnalysisGroupedErrorRunFingerprint(
        arguments=arguments,
        result=LogAnalysisGroupedErrorsResult.model_validate(
            {
                "action": McpToolName.GROUP_ERRORS,
                "project_name": project_name,
                "searched_source_keys": source_keys or [],
                "groups": groups or [],
            }
        ),
    )


def _fingerprints(payload: dict[str, object]) -> LogAnalysisFingerprints:
    return LogAnalysisFingerprints.model_validate(payload)


def _coverage_snapshot(collect_logs: CollectLogsArtifact) -> dict[str, object]:
    return LogAnalysisFingerprintBuilder.build_coverage_snapshot(collect_logs)


def test_history_comparison_service_compares_grouped_errors_with_yesterday() -> None:
    previous_analysis = LogAnalysisOut(
        id=1,
        created_at=datetime(2026, 5, 31, tzinfo=UTC),
        analysis_date=date(2026, 5, 31),
        status="succeeded",
        summary="Known scanner noise.",
        severity=LogAnalysisSeverity.INFO,
        fingerprints=_fingerprints(
            {
                "grouped_error_runs": [
                    {
                        "arguments": {
                            "project_name": "landingpage",
                            "source_keys": ["backend"],
                        },
                        "result": {
                            "groups": [
                                {
                                    "fingerprint": "backend:http_4xx:404:/robots.txt",
                                    "project_name": "landingpage",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 1,
                                    "source_keys": ["backend"],
                                    "request_paths": ["/robots.txt"],
                                    "status_codes": [404],
                                    "levels": [],
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        evidence_fingerprints=[],
        known_patterns=[],
        coverage_snapshot={},
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    current_grouped_error_runs = [
        _grouped_error_run(
            source_keys=["backend", "nginx"],
            groups=[
                {
                    "fingerprint": "nginx:http_4xx:404:/.env",
                    "project_name": "landingpage",
                    "category": "http_4xx",
                    "severity": "medium",
                    "count": 3,
                    "source_keys": ["nginx"],
                    "request_paths": ["/.env"],
                    "status_codes": [404],
                    "levels": [],
                }
            ],
        )
    ]
    service = LogAnalysisHistoryComparisonService()

    result = service.compare_grouped_errors(
        previous_grouped_errors=previous_analysis.fingerprints.grouped_error_runs,
        current_grouped_errors=current_grouped_error_runs,
    )

    assert result is not None
    assert result.new_fingerprints == ["nginx:http_4xx:404:/.env"]
    assert result.resolved_fingerprints == ["backend:http_4xx:404:/robots.txt"]


def test_history_comparison_keeps_empty_current_group_errors_as_evidence() -> None:
    previous_analysis = LogAnalysisOut(
        id=1,
        created_at=datetime(2026, 5, 31, tzinfo=UTC),
        analysis_date=date(2026, 5, 31),
        status="succeeded",
        summary="Known scanner noise.",
        severity=LogAnalysisSeverity.INFO,
        fingerprints=_fingerprints(
            {
                "grouped_error_runs": [
                    {
                        "arguments": {
                            "project_name": "landingpage",
                            "source_keys": ["backend"],
                        },
                        "result": {
                            "groups": [
                                {
                                    "fingerprint": "backend:http_4xx:404:/robots.txt",
                                    "project_name": "landingpage",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 1,
                                    "source_keys": ["backend"],
                                    "request_paths": ["/robots.txt"],
                                    "status_codes": [404],
                                    "levels": [],
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        evidence_fingerprints=[],
        known_patterns=[],
        coverage_snapshot={},
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    current_grouped_error_runs = [_grouped_error_run(source_keys=["backend"])]
    service = LogAnalysisHistoryComparisonService()

    result = service.compare_grouped_errors(
        previous_grouped_errors=previous_analysis.fingerprints.grouped_error_runs,
        current_grouped_errors=current_grouped_error_runs,
    )

    assert result is not None
    assert result.available is True
    assert result.previous_group_count == 1
    assert result.current_group_count == 0
    assert result.new_fingerprints == []
    assert result.resolved_fingerprints == ["backend:http_4xx:404:/robots.txt"]
    assert result.current_tool_scope_by_project == {"landingpage": ["backend"]}


def test_history_comparison_builds_compact_grouped_error_delta() -> None:
    previous_analysis = PreviousLogAnalysisContext.from_analysis(
        LogAnalysisOut(
            id=1,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
            analysis_date=date(2026, 5, 31),
            status="succeeded",
            summary="Known scanner noise.",
            severity="INFO",
            fingerprints=_fingerprints(
                {
                    "grouped_error_runs": [
                        {
                            "result": {
                                "groups": [
                                    {
                                        "fingerprint": "frontend:http_4xx:404:/favicon.png",
                                        "project_name": "landingpage",
                                        "category": "http_4xx",
                                        "severity": "medium",
                                        "count": 4,
                                        "source_keys": ["frontend"],
                                        "request_paths": ["/favicon.png"],
                                        "status_codes": [404],
                                        "levels": [],
                                        "message_summary": (
                                            "Previous message stripped from prompt."
                                        ),
                                        "first_timestamp": "2026-05-31T00:00:00Z",
                                        "last_timestamp": "2026-05-31T01:00:00Z",
                                        "first_seen": {
                                            "line": "previous first line",
                                            "line_number": 1,
                                            "line_truncated": False,
                                            "output_file": "previous.log",
                                            "source_key": "frontend",
                                        },
                                        "last_seen": {
                                            "line": "previous last line",
                                            "line_number": 2,
                                            "line_truncated": False,
                                            "output_file": "previous.log",
                                            "source_key": "frontend",
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                }
            ),
            evidence_fingerprints=[],
            known_patterns=[],
            coverage_snapshot={},
            fingerprint_version="log-analysis-fingerprint-v1",
        )
    )
    current_grouped_error_runs = [
        _grouped_error_run(
            source_keys=["frontend"],
            groups=[
                {
                    "fingerprint": "frontend:http_4xx:404:/favicon.png",
                    "project_name": "landingpage",
                    "category": "http_4xx",
                    "severity": "medium",
                    "count": 6,
                    "source_keys": ["frontend"],
                    "request_paths": ["/favicon.png"],
                    "status_codes": [404],
                    "levels": [],
                    "message_summary": "Long current message that must not reach the prompt.",
                    "first_timestamp": "2026-06-01T00:00:00Z",
                    "last_timestamp": "2026-06-01T01:00:00Z",
                    "first_seen": {
                        "line": "current first line",
                        "line_number": 3,
                        "line_truncated": False,
                        "output_file": "current.log",
                        "source_key": "frontend",
                    },
                    "last_seen": {
                        "line": "current last line",
                        "line_number": 4,
                        "line_truncated": False,
                        "output_file": "current.log",
                        "source_key": "frontend",
                    },
                }
            ],
        )
    ]

    comparison = LogAnalysisHistoryComparisonService().build_grouped_error_comparison(
        previous_grouped_error_runs=previous_analysis.fingerprints.grouped_error_runs,
        current_grouped_error_runs=current_grouped_error_runs,
    )

    assert comparison is not None
    assert comparison.worsened_fingerprints == ["frontend:http_4xx:404:/favicon.png"]
    assert comparison.current_changed_groups[0].count == 6
    assert comparison.previous_changed_groups[0].count == 4
    assert (
        comparison.current_changed_groups[0].message_summary
        == "Long current message that must not reach the prompt."
    )
    assert (
        comparison.previous_changed_groups[0].message_summary
        == "Previous message stripped from prompt."
    )
    assert comparison.current_changed_groups[0].first_timestamp is None
    assert comparison.previous_changed_groups[0].last_timestamp is None
    assert comparison.current_changed_groups[0].first_seen is None
    assert comparison.previous_changed_groups[0].last_seen is None


def test_history_comparison_flags_resolved_high_severity_grouped_errors() -> None:
    previous_analysis = PreviousLogAnalysisContext.from_analysis(
        LogAnalysisOut(
            id=1,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
            analysis_date=date(2026, 5, 31),
            status="succeeded",
            summary="Backend had 500s.",
            severity="INFO",
            fingerprints=_fingerprints(
                {
                    "grouped_error_runs": [
                        {
                            "arguments": {
                                "project_name": "landingpage",
                                "source_keys": ["backend"],
                            },
                            "result": {
                                "groups": [
                                    {
                                        "fingerprint": "backend:http_5xx:500:/api",
                                        "project_name": "landingpage",
                                        "category": "http_5xx",
                                        "severity": "high",
                                        "count": 15,
                                        "source_keys": ["backend"],
                                        "request_paths": ["/api"],
                                        "status_codes": [500],
                                        "levels": [],
                                    }
                                ]
                            },
                        }
                    ]
                }
            ),
            evidence_fingerprints=[],
            known_patterns=[],
            coverage_snapshot={},
            fingerprint_version="log-analysis-fingerprint-v1",
        )
    )
    current_grouped_error_runs = [_grouped_error_run(source_keys=["backend"])]

    comparison = LogAnalysisHistoryComparisonService().build_grouped_error_comparison(
        previous_grouped_error_runs=previous_analysis.fingerprints.grouped_error_runs,
        current_grouped_error_runs=current_grouped_error_runs,
    )

    assert comparison is not None
    assert comparison.resolved_fingerprints == ["backend:http_5xx:500:/api"]
    assert comparison.resolved_high_severity_fingerprints == ["backend:http_5xx:500:/api"]
    assert comparison.resolved_high_severity_tool_scope_by_project == {"landingpage": ["backend"]}
    assert comparison.resolved_high_severity_current_scope_covered is True
    compact = LogAnalysisHistoryComparisonService.compact_grouped_error_comparison_for_prompt(
        comparison
    )
    assert compact.resolved_high_severity_fingerprint_count == 1
    assert compact.resolved_high_severity_fingerprints == ["backend:http_5xx:500:/api"]
    assert compact.resolved_high_severity_tool_scope_by_project == {"landingpage": ["backend"]}
    assert compact.resolved_high_severity_current_scope_covered is True
    assert compact.evidence_quality_warnings == [
        "previous_high_severity_grouped_error_fingerprints_absent_from_current"
    ]
    assert compact.next_evidence_hint == (
        "call_tools_for_broader_current_evidence_before_final_report"
    )


def test_history_comparison_warns_when_resolved_high_severity_scope_is_uncovered() -> None:
    previous_analysis = PreviousLogAnalysisContext.from_analysis(
        LogAnalysisOut(
            id=1,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
            analysis_date=date(2026, 5, 31),
            status="succeeded",
            summary="Backend had 500s.",
            severity="INFO",
            fingerprints=_fingerprints(
                {
                    "grouped_error_runs": [
                        {
                            "arguments": {
                                "project_name": "landingpage",
                                "source_keys": ["backend"],
                            },
                            "result": {
                                "groups": [
                                    {
                                        "fingerprint": "backend:http_5xx:500:/api",
                                        "project_name": "landingpage",
                                        "category": "http_5xx",
                                        "severity": "high",
                                        "count": 15,
                                        "source_keys": ["backend"],
                                        "request_paths": ["/api"],
                                        "status_codes": [500],
                                        "levels": [],
                                    }
                                ]
                            },
                        }
                    ]
                }
            ),
            evidence_fingerprints=[],
            known_patterns=[],
            coverage_snapshot={},
            fingerprint_version="log-analysis-fingerprint-v1",
        )
    )
    current_grouped_error_runs = [_grouped_error_run(source_keys=["nginx"])]

    comparison = LogAnalysisHistoryComparisonService().build_grouped_error_comparison(
        previous_grouped_error_runs=previous_analysis.fingerprints.grouped_error_runs,
        current_grouped_error_runs=current_grouped_error_runs,
    )

    assert comparison is not None
    assert comparison.resolved_high_severity_tool_scope_by_project == {"landingpage": ["backend"]}
    assert comparison.current_tool_scope_by_project == {"landingpage": ["nginx"]}
    assert comparison.resolved_high_severity_current_scope_covered is False
    compact = LogAnalysisHistoryComparisonService.compact_grouped_error_comparison_for_prompt(
        comparison
    )
    assert compact.resolved_high_severity_current_scope_covered is False
    assert compact.evidence_quality_warnings == [
        "previous_high_severity_grouped_error_fingerprints_absent_from_current"
    ]
    assert compact.next_evidence_hint == (
        "call_tools_for_broader_current_evidence_before_final_report"
    )


def test_history_comparison_compacts_grouped_error_delta_for_prompt() -> None:
    comparison = LogAnalysisGroupedErrorComparison(
        available=True,
        current_tool_scope_by_project={"landingpage": ["backend", "nginx"]},
        previous_group_count=20,
        current_group_count=30,
        new_fingerprints=[f"backend:http_4xx:404:/new-{index}" for index in range(12)],
        resolved_fingerprints=["backend:http_4xx:404:/gone"],
        persisting_fingerprints=[f"backend:http_4xx:404:/same-{index}" for index in range(40)],
        worsened_fingerprints=[f"backend:http_4xx:404:/worse-{index}" for index in range(9)],
        improved_fingerprints=["backend:http_4xx:404:/better"],
        new_high_severity_fingerprints=["backend:http_5xx:500:/api"],
        resolved_high_severity_fingerprints=["backend:http_5xx:500:/gone"],
        resolved_high_severity_tool_scope_by_project={"landingpage": ["backend"]},
        current_changed_groups=[
            LogAnalysisGroupedErrorSignal(
                fingerprint=f"backend:http_4xx:404:/new-{index}",
                project_name="landingpage",
                category="http_4xx",
                severity="medium",
                count=index + 1,
                source_keys=["backend"],
                request_paths=[f"/new-{index}"],
                status_codes=[404],
            )
            for index in range(12)
        ],
        previous_changed_groups=[
            LogAnalysisGroupedErrorSignal(
                fingerprint=f"backend:http_4xx:404:/old-{index}",
                project_name="landingpage",
                category="http_4xx",
                severity="medium",
                count=index + 1,
                source_keys=["backend"],
                request_paths=[f"/old-{index}"],
                status_codes=[404],
            )
            for index in range(12)
        ],
        rationale="Full deterministic comparison is available outside the prompt.",
    )

    compact = LogAnalysisHistoryComparisonService.compact_grouped_error_comparison_for_prompt(
        comparison
    )

    assert compact.available is True
    assert compact.current_tool_scope_by_project == {"landingpage": ["backend", "nginx"]}
    assert compact.previous_group_count == 20
    assert compact.current_group_count == 30
    assert compact.new_fingerprint_count == 12
    assert compact.resolved_fingerprint_count == 1
    assert compact.persisting_fingerprint_count == 40
    assert compact.worsened_fingerprint_count == 9
    assert compact.improved_fingerprint_count == 1
    assert compact.new_high_severity_fingerprint_count == 1
    assert compact.new_high_severity_fingerprints == ["backend:http_5xx:500:/api"]
    assert compact.resolved_high_severity_fingerprint_count == 1
    assert compact.resolved_high_severity_fingerprints == ["backend:http_5xx:500:/gone"]
    assert compact.resolved_high_severity_tool_scope_by_project == {"landingpage": ["backend"]}
    assert compact.resolved_high_severity_current_scope_covered is True
    assert compact.evidence_quality_warnings == [
        "worsened_grouped_error_fingerprints_present",
        "new_high_severity_grouped_error_fingerprints_present",
        "previous_high_severity_grouped_error_fingerprints_absent_from_current",
    ]
    assert compact.next_evidence_hint == (
        "call_tools_for_broader_current_evidence_before_final_report"
    )
    assert len(compact.current_changed_examples) == 8
    assert len(compact.previous_changed_examples) == 8
    dumped = compact.model_dump(mode="json")
    assert "new_fingerprints" not in dumped
    assert "worsened_fingerprints" not in dumped
    assert "current_changed_groups" not in dumped


def test_history_comparison_flags_empty_grouped_error_baseline_for_prompt() -> None:
    comparison = LogAnalysisGroupedErrorComparison(
        available=True,
        current_tool_scope_by_project={"landingpage": ["backend"]},
        previous_group_count=0,
        current_group_count=1,
        new_fingerprints=["backend:http_4xx:404:/robots.txt"],
        current_changed_groups=[
            LogAnalysisGroupedErrorSignal(
                fingerprint="backend:http_4xx:404:/robots.txt",
                project_name="landingpage",
                category="http_4xx",
                severity="medium",
                count=1,
                source_keys=["backend"],
                request_paths=["/robots.txt"],
                status_codes=[404],
            )
        ],
        rationale="Full deterministic comparison is available outside the prompt.",
    )

    compact = LogAnalysisHistoryComparisonService.compact_grouped_error_comparison_for_prompt(
        comparison
    )

    assert compact.previous_group_count == 0
    assert compact.current_group_count == 1
    assert compact.new_fingerprint_count == 1
    assert compact.evidence_quality_warnings == [
        "previous_grouped_error_baseline_empty",
        "all_current_grouped_error_fingerprints_are_new",
    ]
    assert compact.next_evidence_hint == (
        "call_tools_for_broader_current_evidence_before_final_report"
    )


def test_history_comparison_builds_missing_log_guard() -> None:
    previous_analysis = PreviousLogAnalysisContext.from_analysis(
        LogAnalysisOut(
            id=1,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
            analysis_date=date(2026, 5, 31),
            status="succeeded",
            summary="Known scanner noise.",
            severity="INFO",
            fingerprints=_fingerprints({}),
            evidence_fingerprints=[],
            known_patterns=[],
            coverage_snapshot={
                "projects": [
                    {
                        "project_name": "landingpage",
                        "sources": [
                            {
                                "source_key": "nginx",
                                "status": "collected",
                                "line_count": 10,
                                "zero_lines": False,
                            }
                        ],
                    }
                ]
            },
            fingerprint_version="log-analysis-fingerprint-v1",
        )
    )

    current_collect_logs = CollectLogsArtifact.model_validate(
        build_collect_logs_artifact_payload(
            resolved_source_keys=["backend", "nginx"],
            include_unavailable_nginx=True,
        )
    )

    comparison = LogAnalysisHistoryComparisonService().build_missing_source_comparison(
        previous_coverage_snapshot=previous_analysis.coverage_snapshot.model_dump(mode="json"),
        current_coverage_snapshot=_coverage_snapshot(current_collect_logs),
        previous_severity=previous_analysis.severity,
    )

    assert comparison.source_coverage_changed is True
    assert comparison.changed_sources == ["landingpage.nginx"]
    assert comparison.tool_scope_by_project == {"landingpage": ["nginx"]}
    assert comparison.recommended_action == RecommendedAction.CALL_TOOLS


def test_history_comparison_finds_unsupported_broad_report_claims() -> None:
    collect_logs = CollectLogsArtifact.model_validate(build_collect_logs_artifact_payload())
    prompt_context = LogAnalysisPromptContext(
        analysis_date=date(2026, 6, 4),
        workflow_name="analyze_daily_log_bundle",
        current_phase=LogAnalysisPromptPhase.INSPECT_COLLECTED_LOGS,
        completed_steps=["collect_logs"],
        evidence={
            "history_comparison": {"status": "available"},
            "prompt_compacted": {
                "grouped_error_diff": {
                    "available": True,
                    "current_tool_scope_by_project": {"landingpage": ["backend"]},
                    "previous_group_count": 1,
                    "current_group_count": 1,
                    "rationale": "Grouped errors were compared.",
                },
            },
        },
        current_coverage=LogAnalysisCurrentCoverage(),
        evidence_mode=LogAnalysisEvidenceMode.CURRENT_TOOL_RESULTS_AVAILABLE,
        allowed_actions=[
            LogAnalysisAllowedAction.CALL_TOOLS,
            LogAnalysisAllowedAction.READ_SKILLS,
            LogAnalysisAllowedAction.FINAL_REPORT,
        ],
        next_required_action=LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION,
        final_report_allowed=True,
        mandatory_skills=[],
        collection=LogAnalysisPromptCollection(
            action=McpToolName.COLLECT_LOGS,
            workspace=LogWorkspace.WORKFLOW,
            session_id=collect_logs.session_id,
            projects=[],
        ),
        snapshot_access=SnapshotAccessGuidance(
            workspace=LogWorkspace.WORKFLOW,
            session_id=collect_logs.session_id,
            session_id_is_for_session_workspace_only=True,
            workflow_followup_arguments=["project_name", "archive_name"],
            instruction="Use project_name for workflow follow-up tools.",
        ),
        report_contract={},
    )
    final_report = LogAnalysisFinalReport(
        action="final_report",
        summary="All projects show stable operation with no 5xx errors.",
        severity=LogAnalysisSeverity.INFO,
        severity_rationale="No service impact was found across all sources.",
        key_findings=[],
        evidence=[],
        coverage_gaps=[],
        recommendations="Continue monitoring.",
        watch_only_items=[],
        trend_summary="Previous analysis was stable.",
    )

    unsupported_claims = (
        LogAnalysisHistoryComparisonService.find_unsupported_history_comparison_claims(
            final_report=final_report,
            prompt_context=prompt_context,
        )
    )

    assert unsupported_claims == [
        "Final report makes a broad current-run health claim outside the "
        "current grouped-error evidence scope."
    ]
