from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from mcp import McpWorkflowClient
from schemas import (
    CollectLogsArtifact,
    LogAnalysisGroupedErrorsResult,
    McpToolName,
    ProjectManifestSummary,
    WorkflowBootstrap,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "log_analysis_mcp"


def load_log_analysis_mcp_fixture(
    scenario: str,
    name: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load one phase-9 MCP fixture payload and apply narrow recursive overrides."""

    path: Path = FIXTURE_ROOT / scenario / f"{name}.json"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if overrides:
        _deep_update(payload, overrides)
    return payload


def load_workflow_bootstrap_fixture(
    *,
    overrides: dict[str, Any] | None = None,
) -> WorkflowBootstrap:
    """Load and validate the shared MCP workflow-bootstrap fixture."""

    payload: dict[str, Any] = load_log_analysis_mcp_fixture(
        "common",
        "workflow_bootstrap",
        overrides=overrides,
    )
    return WorkflowBootstrap.model_validate(payload)


def load_collect_logs_fixture(
    *,
    since: str,
    until: str,
    session_id: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> CollectLogsArtifact:
    """Load and validate the shared collect_logs fixture with per-run timestamps."""

    payload_overrides: dict[str, Any] = {
        "session_id": session_id,
        "projects": [
            {
                "requested_since": since,
                "requested_until": until,
            }
        ],
    }
    if overrides:
        _deep_update(payload_overrides, overrides)
    payload: dict[str, Any] = load_log_analysis_mcp_fixture(
        "common",
        "collect_logs_base",
        overrides=payload_overrides,
    )
    return CollectLogsArtifact.model_validate(payload)


def load_project_manifest_fixture(
    *,
    overrides: dict[str, Any] | None = None,
) -> list[ProjectManifestSummary]:
    """Load and validate the shared list_projects fixture."""

    payload: dict[str, Any] = load_log_analysis_mcp_fixture(
        "common",
        "list_projects",
        overrides=overrides,
    )
    projects: object = payload["result"]
    if not isinstance(projects, list):
        raise TypeError("list_projects fixture must contain a list result.")
    return [ProjectManifestSummary.model_validate(project) for project in projects]


class FixtureBackedMcpWorkflowClient(McpWorkflowClient):
    """MCP workflow client backed by phase-9 JSON fixtures."""

    def __init__(
        self,
        *,
        scenario: str,
        session_id: str | None = "fixture-session-id",
        collect_logs_overrides: dict[str, Any] | None = None,
        tool_result_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            base_url="http://mcp.test/mcp",
            workflow_jwt="test-workflow-jwt",
        )
        self.scenario = scenario
        self.session_id = session_id
        self.collect_logs_overrides = collect_logs_overrides or {}
        self.tool_result_overrides = tool_result_overrides or {}
        self.calls: list[str] = []
        self.called_tool_names: list[str] = []

    async def get_workflow_bundle(self) -> WorkflowBootstrap:
        self.calls.append("get_workflow_bundle")
        return load_workflow_bootstrap_fixture()

    async def read_resource(self, uri: str) -> str:
        self.calls.append(f"read_resource:{uri}")
        skill_body_by_uri: dict[str, str] = {
            "skill://workflow/severity_guide": "Severity guide skill body.",
            "skill://workflow/bot_detection": "Bot detection skill body.",
            "skill://workflow/owasp_security": "OWASP security skill body.",
        }
        return skill_body_by_uri[uri]

    async def list_projects(self) -> list[ProjectManifestSummary]:
        self.calls.append(McpToolName.LIST_PROJECTS)
        return load_project_manifest_fixture()

    async def collect_logs(
        self,
        *,
        since: str,
        until: str,
    ) -> CollectLogsArtifact:
        self.calls.append(f"collect_logs:{since}:{until}")
        return load_collect_logs_fixture(
            since=since,
            until=until,
            session_id=self.session_id,
            overrides=self.collect_logs_overrides,
        )

    async def call_deterministic_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(f"call_deterministic_tool:{name}:{arguments}")
        self.called_tool_names.append(name)
        result_name: str = _fixture_name_for_tool(name, arguments)
        result: dict[str, Any] = load_log_analysis_mcp_fixture(
            self.scenario,
            result_name,
            overrides=self.tool_result_overrides.get(name),
        )
        if name == McpToolName.GROUP_ERRORS:
            LogAnalysisGroupedErrorsResult.from_mcp_payload(result)
        return result


def _fixture_name_for_tool(name: str, arguments: dict[str, Any]) -> str:
    if name == McpToolName.GROUP_ERRORS:
        if arguments.get("project_name") == "vps-security":
            return "group_errors_vps_security"
        return "group_errors"
    if name == McpToolName.INSPECT_PROXY_ACTIVITY:
        return "inspect_proxy_activity"
    if name == McpToolName.BUILD_INCIDENT_BUNDLE:
        return "incident_bundle"
    if name == McpToolName.GREP_LOG_SNAPSHOT:
        pattern: str = str(arguments.get("pattern") or "")
        if ".env" in pattern:
            return "grep_snapshot_env_200"
        return "grep_snapshot"
    raise KeyError(f"No fixture is registered for MCP tool {name!r}.")


def _deep_update(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        elif (
            isinstance(value, list)
            and isinstance(target.get(key), list)
            and _can_merge_list(target[key], value)
        ):
            for index, item in enumerate(value):
                if isinstance(item, dict) and isinstance(target[key][index], dict):
                    _deep_update(target[key][index], item)
                else:
                    target[key][index] = copy.deepcopy(item)
        else:
            target[key] = copy.deepcopy(value)


def _can_merge_list(target: list[Any], updates: list[Any]) -> bool:
    return len(updates) <= len(target)
