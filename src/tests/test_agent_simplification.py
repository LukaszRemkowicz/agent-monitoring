from typing import Any, cast

import pytest
from llm_core.providers.mock import MockProvider
from pydantic import BaseModel, ValidationError

from agents import MonitoringWorkflowAgent
from schemas import (
    GroupErrorsArgumentsModel,
    GroupErrorsResponseModel,
    LogAnalysisGroupedErrorEvidenceLabel,
    LogAnalysisGroupedErrorSignal,
    McpToolName,
)


def _agent(mcp_client: object | None = None) -> MonitoringWorkflowAgent:
    return MonitoringWorkflowAgent(
        mcp_client=cast(Any, mcp_client or object()),
        llm_provider=MockProvider(),
        private_monitoring_context="private",
    )


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
