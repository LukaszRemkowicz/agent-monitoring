import copy
import json
from datetime import date
from string import ascii_lowercase
from typing import Any, cast

import pytest
from llm_core.providers.mock import MockProvider
from pydantic import BaseModel, ValidationError

from agents import (
    LogAnalysisModelTier,
    MonitoringWorkflowAgent,
    select_log_analysis_model_route,
)
from schemas import (
    CollectLogsArtifact,
    GroupErrorsArgumentsModel,
    GroupErrorsResponseModel,
    LogAnalysisCurrentCoverage,
    LogAnalysisFingerprints,
    LogAnalysisGroupedErrorEvidenceLabel,
    LogAnalysisGroupedErrorRunFingerprint,
    LogAnalysisGroupedErrorSignal,
    LogAnalysisGroupedErrorsResult,
    LogAnalysisHistoryComparisonStatus,
    LogAnalysisPromptCompactedEvidence,
    LogAnalysisPromptEvidence,
    LogAnalysisPromptEvidenceKind,
    LogAnalysisPromptGroupedErrorComparison,
    LogAnalysisPromptGroupedErrorEvidence,
    LogAnalysisPromptHistoryComparisonState,
    LogAnalysisSeverity,
    McpToolName,
    PreviousLogAnalysisContext,
)
from services.log_fingerprints import LOG_ANALYSIS_FINGERPRINT_VERSION
from services.log_history_comparison import LogAnalysisHistoryComparisonService
from tests.conftest import build_collect_logs_artifact_payload


def _agent(mcp_client: object | None = None) -> MonitoringWorkflowAgent:
    return MonitoringWorkflowAgent(
        mcp_client=cast(Any, mcp_client or object()),
        llm_provider=MockProvider(),
        private_monitoring_context="private",
    )


def _routing_evidence(
    *,
    attention_counts: dict[str, int] | None = None,
    evidence_complete: bool = True,
    compare_history: bool = False,
    new_fingerprints: list[str] | None = None,
    worsened_fingerprints: list[str] | None = None,
) -> LogAnalysisPromptEvidence:
    current = LogAnalysisPromptGroupedErrorEvidence(
        available=True,
        label=LogAnalysisGroupedErrorEvidenceLabel.CURRENT,
        evidence_complete=evidence_complete,
        attention_priority_counts=attention_counts or {},
        unique_group_count=sum((attention_counts or {}).values()),
    )
    if not compare_history:
        return LogAnalysisPromptEvidence(
            kind=LogAnalysisPromptEvidenceKind.GROUPED_ERROR_BASELINE,
            current_grouped_errors=current,
        )
    new = new_fingerprints or []
    worsened = worsened_fingerprints or []
    return LogAnalysisPromptEvidence(
        kind=LogAnalysisPromptEvidenceKind.HISTORY_COMPARISON,
        history_comparison=LogAnalysisPromptHistoryComparisonState(
            status=LogAnalysisHistoryComparisonStatus.AVAILABLE,
        ),
        current_grouped_errors=current,
        prompt_compacted=LogAnalysisPromptCompactedEvidence(
            grouped_error_diff=LogAnalysisPromptGroupedErrorComparison(
                available=True,
                new_fingerprint_count=len(new),
                new_fingerprints=new,
                worsened_fingerprint_count=len(worsened),
                worsened_fingerprints=worsened,
                rationale="Deterministic routing fixture.",
            )
        ),
    )


def test_current_coverage_preserves_collection_warnings_and_unknown_sources() -> None:
    payload = build_collect_logs_artifact_payload()
    project = payload["projects"][0]
    project["warnings"] = ["Collection completed with partial provenance."]
    project["unknown_requested_source_keys"] = ["missing"]

    coverage = MonitoringWorkflowAgent._build_current_coverage(
        CollectLogsArtifact.model_validate(payload)
    )

    assert coverage.collection_warnings == ["Collection completed with partial provenance."]
    assert coverage.unknown_requested_sources == ["demo-shop.missing"]


@pytest.mark.parametrize(
    ("evidence", "coverage", "expected_tier"),
    [
        (
            _routing_evidence(),
            LogAnalysisCurrentCoverage(),
            LogAnalysisModelTier.FAST,
        ),
        (
            _routing_evidence(
                attention_counts={"watch_only": 1},
                compare_history=True,
            ),
            LogAnalysisCurrentCoverage(),
            LogAnalysisModelTier.FAST,
        ),
        (
            _routing_evidence(attention_counts={"actionable": 1}),
            LogAnalysisCurrentCoverage(),
            LogAnalysisModelTier.STRONG,
        ),
        (
            _routing_evidence(attention_counts={"investigate": 1}),
            LogAnalysisCurrentCoverage(),
            LogAnalysisModelTier.STRONG,
        ),
        (
            _routing_evidence(evidence_complete=False),
            LogAnalysisCurrentCoverage(),
            LogAnalysisModelTier.STRONG,
        ),
        (
            _routing_evidence(),
            LogAnalysisCurrentCoverage(unavailable_sources=["demo.backend"]),
            LogAnalysisModelTier.STRONG,
        ),
        (
            _routing_evidence(),
            LogAnalysisCurrentCoverage(
                collection_warnings=["Collection completed with partial provenance."],
            ),
            LogAnalysisModelTier.STRONG,
        ),
        (
            _routing_evidence(),
            LogAnalysisCurrentCoverage(unknown_requested_sources=["demo.missing"]),
            LogAnalysisModelTier.STRONG,
        ),
        (
            _routing_evidence(
                attention_counts={"watch_only": 1},
                compare_history=True,
                new_fingerprints=["opaque-new-family"],
            ),
            LogAnalysisCurrentCoverage(),
            LogAnalysisModelTier.STRONG,
        ),
        (
            _routing_evidence(
                attention_counts={"watch_only": 1},
                compare_history=True,
                worsened_fingerprints=["opaque-worsened-family"],
            ),
            LogAnalysisCurrentCoverage(),
            LogAnalysisModelTier.STRONG,
        ),
    ],
)
def test_log_analysis_model_routing_fails_closed_on_risk(
    evidence: LogAnalysisPromptEvidence,
    coverage: LogAnalysisCurrentCoverage,
    expected_tier: LogAnalysisModelTier,
) -> None:
    route = select_log_analysis_model_route(evidence=evidence, current_coverage=coverage)

    assert route.tier is expected_tier
    assert route.reasons


def test_grouped_error_prompt_normalizes_high_cardinality_route_variants() -> None:
    groups = [
        LogAnalysisGroupedErrorSignal(
            fingerprint=f"backend:http_4xx:404:/orders/{index}",
            project_name="demo",
            category="http_4xx",
            severity="medium",
            count=1,
            source_keys=["backend"],
            request_paths=[f"/orders/{index}"],
            request_methods=["GET"],
            status_codes=[404],
        )
        for index in range(100)
    ]

    evidence = _agent()._compact_grouped_error_baseline_for_prompt(
        label=LogAnalysisGroupedErrorEvidenceLabel.CURRENT,
        groups=groups,
        run_count=1,
        tool_scope_by_project={"demo": ["backend"]},
        rationale="Current grouped errors.",
    )

    assert evidence is not None
    assert len(evidence.fingerprints) == 1
    assert evidence.fingerprints[0].variant_count == 100
    assert evidence.fingerprints[0].count == 100
    assert evidence.fingerprints[0].request_paths == ["/orders/{id}"]
    serialized = evidence.model_dump_json()
    assert "/orders/99" not in serialized


@pytest.mark.parametrize("identity_kind", ["explicit_message", "raw_fallback"])
def test_grouped_error_prompt_preserves_complete_message_summary(
    identity_kind: str,
) -> None:
    full_summary = "x" * 2_000
    signal = LogAnalysisGroupedErrorSignal(
        fingerprint="backend:application_error:v2:one",
        project_name="demo",
        category="application_error",
        source_keys=["backend"],
        identity_kind=identity_kind,
        message_summary=full_summary,
    )

    evidence = _agent()._compact_grouped_error_baseline_for_prompt(
        label=LogAnalysisGroupedErrorEvidenceLabel.CURRENT,
        groups=[signal],
        run_count=1,
        tool_scope_by_project={"demo": ["backend"]},
        rationale="Current grouped errors.",
    )

    assert evidence is not None
    assert evidence.fingerprints[0].message_summary == full_summary
    assert signal.message_summary == full_summary


def test_grouped_error_prompt_bounds_details_without_hiding_family_identities() -> None:
    groups: list[LogAnalysisGroupedErrorSignal] = []
    expected_fingerprints: set[str] = set()
    for index in range(30):
        suffix = f"{ascii_lowercase[index % 26]}{index}"
        signals = [
            LogAnalysisGroupedErrorSignal(
                fingerprint=f"backend:http_5xx:500:/api-{suffix}",
                project_name="demo",
                category="http_5xx",
                severity="high",
                count=index + 1,
                source_keys=["backend"],
                request_paths=[f"/api-{suffix}"],
                status_codes=[500],
                message_summary="Service failure " + ("x" * 160),
            ),
            LogAnalysisGroupedErrorSignal(
                fingerprint=f"backend:http_4xx:404:/missing-{suffix}",
                project_name="demo",
                category="http_4xx",
                severity="medium",
                count=index + 1,
                source_keys=["backend"],
                request_paths=[f"/missing-{suffix}"],
                status_codes=[404],
                message_summary="Missing route " + ("x" * 160),
            ),
            LogAnalysisGroupedErrorSignal(
                fingerprint=f"traefik:http_4xx:404:/.env.backup-{suffix}",
                project_name="host-security",
                category="http_4xx",
                severity="medium",
                count=index + 1,
                source_keys=["traefik"],
                request_paths=[f"/.env.backup-{suffix}"],
                status_codes=[404],
                upstream_attempted=False,
                message_summary="Blocked probe " + ("x" * 160),
            ),
            LogAnalysisGroupedErrorSignal(
                fingerprint=f"scheduler:info:/health-{suffix}",
                project_name="demo",
                category="informational",
                severity="low",
                count=index + 1,
                source_keys=["scheduler"],
                request_paths=[f"/health-{suffix}"],
                status_codes=[200],
                message_summary="Routine event " + ("x" * 160),
            ),
        ]
        groups.extend(signals)
        expected_fingerprints.update(signal.fingerprint for signal in signals)

    agent = _agent()
    evidence = agent._compact_grouped_error_baseline_for_prompt(
        label=LogAnalysisGroupedErrorEvidenceLabel.CURRENT,
        groups=groups,
        run_count=2,
        tool_scope_by_project={
            "demo": ["backend", "scheduler"],
            "host-security": ["traefik"],
        },
        rationale="Current grouped errors.",
    )
    unbounded = agent._compact_grouped_error_baseline_for_prompt(
        label=LogAnalysisGroupedErrorEvidenceLabel.CURRENT,
        groups=groups,
        run_count=2,
        tool_scope_by_project={
            "demo": ["backend", "scheduler"],
            "host-security": ["traefik"],
        },
        rationale="Current grouped errors.",
        detail_limit_per_attention=10_000,
    )

    assert evidence is not None
    assert unbounded is not None
    assert evidence.unique_group_count == 120
    assert evidence.event_count == 4 * sum(range(1, 31))
    assert len(evidence.fingerprints) == 54
    assert sum(row.attention_priority == "actionable" for row in evidence.fingerprints) == 30
    assert evidence.omitted_details is not None
    assert evidence.omitted_details.count == 66
    assert evidence.omitted_details.counts_by_attention == {
        "investigate": 22,
        "watch_only": 22,
        "routine": 22,
    }
    omitted_fingerprints = {
        fingerprint
        for scopes in evidence.omitted_details.fingerprint_scopes_by_attention.values()
        for scope in scopes
        for fingerprint in scope.fingerprints
    }
    detailed_fingerprints = {row.fingerprint for row in evidence.fingerprints}
    assert detailed_fingerprints.isdisjoint(omitted_fingerprints)
    assert detailed_fingerprints | omitted_fingerprints == expected_fingerprints
    assert not any(
        fingerprint.startswith("backend:http_5xx:500") for fingerprint in omitted_fingerprints
    )
    assert any(row.upstream_attempted is False for row in evidence.fingerprints)
    assert len(evidence.model_dump_json()) < len(unbounded.model_dump_json()) * 0.75


def test_compare_history_prompt_shares_budget_and_avoids_current_detail_duplication() -> None:
    def build_groups(prefix: str) -> list[LogAnalysisGroupedErrorSignal]:
        groups: list[LogAnalysisGroupedErrorSignal] = []
        for index in range(12):
            suffix = f"{prefix}-{ascii_lowercase[index]}{index}"
            groups.extend(
                [
                    LogAnalysisGroupedErrorSignal(
                        fingerprint=f"backend:http_5xx:500:/api-{suffix}",
                        project_name="demo",
                        category="http_5xx",
                        severity="high",
                        count=index + 1,
                        source_keys=["backend"],
                        request_paths=[f"/api-{suffix}"],
                        status_codes=[500],
                        message_summary="Service failure " + ("x" * 160),
                    ),
                    LogAnalysisGroupedErrorSignal(
                        fingerprint=f"backend:http_4xx:404:/missing-{suffix}",
                        project_name="demo",
                        category="http_4xx",
                        severity="medium",
                        count=index + 1,
                        source_keys=["backend"],
                        request_paths=[f"/missing-{suffix}"],
                        status_codes=[404],
                        message_summary="Missing route " + ("x" * 160),
                    ),
                    LogAnalysisGroupedErrorSignal(
                        fingerprint=f"traefik:http_4xx:404:/.env.backup-{suffix}",
                        project_name="host-security",
                        category="http_4xx",
                        severity="medium",
                        count=index + 1,
                        source_keys=["traefik"],
                        request_paths=[f"/.env.backup-{suffix}"],
                        status_codes=[404],
                        upstream_attempted=False,
                        message_summary="Blocked probe " + ("x" * 160),
                    ),
                    LogAnalysisGroupedErrorSignal(
                        fingerprint=f"scheduler:info:/health-{suffix}",
                        project_name="demo",
                        category="informational",
                        severity="low",
                        count=index + 1,
                        source_keys=["scheduler"],
                        request_paths=[f"/health-{suffix}"],
                        status_codes=[200],
                        message_summary="Routine event " + ("x" * 160),
                    ),
                ]
            )
        return groups

    def build_run(
        groups: list[LogAnalysisGroupedErrorSignal],
    ) -> LogAnalysisGroupedErrorRunFingerprint:
        return LogAnalysisGroupedErrorRunFingerprint(
            arguments={"project_name": "all"},
            result=LogAnalysisGroupedErrorsResult(
                project_name="all",
                searched_source_keys=["backend", "scheduler", "traefik"],
                groups=groups,
            ),
        )

    current_groups = build_groups("current")
    previous_groups = build_groups("previous")
    current_runs = [build_run(current_groups)]
    previous_runs = [build_run(previous_groups)]
    history_service = LogAnalysisHistoryComparisonService()
    agent = MonitoringWorkflowAgent(
        mcp_client=cast(Any, object()),
        llm_provider=MockProvider(),
        private_monitoring_context="private",
        history_comparison_service=history_service,
        history_comparison_enabled=True,
    )
    previous_analysis = PreviousLogAnalysisContext(
        analysis_date=date(2026, 8, 5),
        summary="Previous high-cardinality evidence.",
        severity=LogAnalysisSeverity.INFO,
        fingerprints=LogAnalysisFingerprints(
            version=LOG_ANALYSIS_FINGERPRINT_VERSION,
            grouped_error_runs=previous_runs,
        ),
        fingerprint_version=LOG_ANALYSIS_FINGERPRINT_VERSION,
    )

    prepared = agent._prepare_log_analysis_evidence(
        current_grouped_errors=current_runs,
        current_coverage_snapshot={"totals": {}, "projects": []},
        previous_analysis=previous_analysis,
    )

    current_evidence = prepared.current_grouped_errors
    assert current_evidence is not None
    assert len(current_evidence.fingerprints) == 36
    assert prepared.prompt_compacted is not None
    compact_diff = prepared.prompt_compacted.grouped_error_diff
    assert compact_diff is not None
    assert compact_diff.current_changed_examples == []
    assert compact_diff.current_omitted_details is not None
    assert compact_diff.current_omitted_details.count == 48
    assert len(compact_diff.previous_changed_examples) == 36
    assert compact_diff.previous_omitted_details is not None
    assert compact_diff.previous_omitted_details.count == 12
    assert len(current_evidence.fingerprints) + len(compact_diff.previous_changed_examples) == 72

    comparison = history_service.compare_grouped_errors(
        previous_grouped_errors=previous_runs,
        current_grouped_errors=current_runs,
    )
    assert comparison is not None
    unbounded_current = agent._compact_grouped_error_baseline_for_prompt(
        label=LogAnalysisGroupedErrorEvidenceLabel.CURRENT,
        groups=current_groups,
        run_count=1,
        tool_scope_by_project={"all": ["backend", "scheduler", "traefik"]},
        rationale="Current grouped errors.",
        detail_limit_per_attention=10_000,
    )
    assert unbounded_current is not None
    legacy_diff = compact_diff.model_copy(
        update={
            "current_changed_examples": [
                history_service._compact_grouped_error_example(group)
                for group in comparison.current_changed_groups
            ],
            "previous_changed_examples": [
                history_service._compact_grouped_error_example(group)
                for group in comparison.previous_changed_groups
            ],
            "current_omitted_details": None,
            "previous_omitted_details": None,
        }
    )
    legacy_payload = copy.deepcopy(prepared.to_prompt_dict())
    legacy_payload["current_grouped_errors"] = unbounded_current.model_dump(mode="json")
    legacy_payload["prompt_compacted"]["grouped_error_diff"] = legacy_diff.model_dump(mode="json")
    compact_size = len(json.dumps(prepared.to_prompt_dict()))
    legacy_size = len(json.dumps(legacy_payload))
    assert compact_size < legacy_size * 0.75


class _ManyPageMcpClient:
    def __init__(
        self,
        page_count: int,
        *,
        analysis_complete: bool = True,
        incomplete_last_page: bool = False,
        missing_next_offset: bool = False,
        searched_source_keys: list[str] | None = None,
    ) -> None:
        self.page_count = page_count
        self.analysis_complete = analysis_complete
        self.incomplete_last_page = incomplete_last_page
        self.missing_next_offset = missing_next_offset
        self.searched_source_keys = (
            ["backend"] if searched_source_keys is None else searched_source_keys
        )
        self.requested_offsets: list[int] = []

    async def call_deterministic_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        response_model: type[BaseModel],
    ) -> BaseModel:
        assert tool_name == McpToolName.GROUP_ERRORS
        offset = int(arguments.get("offset", 0))
        self.requested_offsets.append(offset)
        next_offset = offset + 1
        truncated = next_offset < self.page_count or self.incomplete_last_page
        return response_model.model_validate(
            {
                "action": McpToolName.GROUP_ERRORS,
                "fingerprint_version": "group-errors-v2",
                "requested_project_name": "demo",
                "project_name": "demo",
                "snapshot_collected_at": "2026-07-29T00:00:00Z",
                "snapshot_dir": "/snapshots/demo",
                "workspace": "workflow",
                "session_id": None,
                "grouped_error_count": self.page_count,
                "matching_line_count": self.page_count,
                "analysis_complete": self.analysis_complete,
                "analysis_group_limit": 5_000,
                "searched_source_keys": self.searched_source_keys,
                "analysis_cautions": [],
                "next_step_tips": [],
                "max_groups": 1,
                "offset": offset,
                "returned_group_count": 1,
                **({} if self.missing_next_offset else {"next_offset": next_offset}),
                "partial_page": self.page_count > 1 or not self.analysis_complete,
                "truncated": truncated,
                "summary": "Grouped errors.",
                "groups": [
                    {
                        "fingerprint": f"backend:v2:application_error:{offset}",
                        "category": "application_error",
                        "severity": "high",
                        "count": 1,
                        "source_keys": ["backend"],
                        "request_paths": [],
                        "request_methods": [],
                        "request_hosts": [],
                        "status_codes": [],
                        "levels": [],
                        "message_summary": "Application error.",
                        "has_explicit_message": True,
                        "identity_kind": "explicit_message",
                        "semantic_summary": "Application error.",
                        "semantic_identity_hash": "",
                        "upstream_attempted": None,
                        "first_timestamp": None,
                        "last_timestamp": None,
                        "first_seen": {
                            "source_key": "backend",
                            "output_file": "/snapshots/demo/backend.log",
                            "line_number": 1,
                            "line": "Application error.",
                            "line_truncated": False,
                        },
                        "last_seen": {
                            "source_key": "backend",
                            "output_file": "/snapshots/demo/backend.log",
                            "line_number": 1,
                            "line": "Application error.",
                            "line_truncated": False,
                        },
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_group_error_pagination_reads_until_mcp_is_complete() -> None:
    mcp_client = _ManyPageMcpClient(page_count=51)

    result = await _agent(mcp_client)._collect_all_group_error_pages(
        arguments=GroupErrorsArgumentsModel(
            project_name="demo",
            source_keys=["backend"],
            max_groups=1,
        ),
    )

    assert isinstance(result, GroupErrorsResponseModel)
    assert result.analysis_complete is True
    assert result.analysis_group_limit == 5_000
    assert result.truncated is False
    assert result.next_offset == 51
    assert len(result.groups) == 51
    assert mcp_client.requested_offsets == list(range(51))


@pytest.mark.asyncio
async def test_group_error_pagination_preserves_incomplete_analysis() -> None:
    result = await _agent(
        _ManyPageMcpClient(page_count=1, analysis_complete=False)
    )._collect_all_group_error_pages(
        arguments=GroupErrorsArgumentsModel(
            project_name="demo",
            source_keys=["backend"],
            max_groups=1,
        ),
    )

    assert result.analysis_complete is False
    assert result.partial_page is True


@pytest.mark.asyncio
async def test_group_error_pagination_rejects_incomplete_final_page() -> None:
    with pytest.raises(ValueError, match="invalid next_offset"):
        await _agent(
            _ManyPageMcpClient(page_count=1, incomplete_last_page=True)
        )._collect_all_group_error_pages(
            arguments=GroupErrorsArgumentsModel(
                project_name="demo",
                source_keys=["backend"],
                max_groups=1,
            ),
        )


@pytest.mark.asyncio
async def test_group_error_pagination_requires_typed_next_offset() -> None:
    with pytest.raises(ValidationError, match="next_offset"):
        await _agent(
            _ManyPageMcpClient(page_count=1, missing_next_offset=True)
        )._collect_all_group_error_pages(
            arguments=GroupErrorsArgumentsModel(
                project_name="demo",
                source_keys=["backend"],
                max_groups=1,
            ),
        )


def test_group_error_response_rejects_null_next_offset() -> None:
    with pytest.raises(ValidationError, match="next_offset"):
        GroupErrorsResponseModel.model_validate(
            {
                "action": McpToolName.GROUP_ERRORS,
                "fingerprint_version": "group-errors-v2",
                "requested_project_name": "demo",
                "project_name": "demo",
                "snapshot_collected_at": "2026-07-29T00:00:00Z",
                "snapshot_dir": "/snapshots/demo",
                "workspace": "workflow",
                "session_id": None,
                "grouped_error_count": 0,
                "matching_line_count": 0,
                "analysis_complete": True,
                "analysis_group_limit": 5_000,
                "searched_source_keys": [],
                "analysis_cautions": [],
                "next_step_tips": [],
                "max_groups": 1,
                "offset": 0,
                "returned_group_count": 0,
                "next_offset": None,
                "partial_page": False,
                "truncated": False,
                "summary": "No grouped errors.",
                "groups": [],
            }
        )


@pytest.mark.asyncio
async def test_group_error_pagination_rejects_narrower_returned_scope() -> None:
    with pytest.raises(ValueError, match="narrower source scope"):
        await _agent(
            _ManyPageMcpClient(page_count=1, searched_source_keys=[])
        )._collect_all_group_error_pages(
            arguments=GroupErrorsArgumentsModel(
                project_name="demo",
                source_keys=["backend"],
                max_groups=1,
            ),
        )
