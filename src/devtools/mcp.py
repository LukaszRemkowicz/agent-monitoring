from __future__ import annotations

import copy
import json
from datetime import date, timedelta
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


class FakerMCP(McpWorkflowClient):
    """Fake MCP workflow client backed by devtool JSON fixtures."""

    fixture_root = Path(__file__).parent / "fixtures" / "log_analysis_mcp"
    fixture_reference_date = date(2026, 5, 19)

    def __init__(
        self,
        *,
        scenario: str,
        session_id: str | None = "fixture-session-id",
        target_analysis_date: date | None = None,
        collect_logs_overrides: dict[str, Any] | None = None,
        tool_result_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            base_url="http://mcp.fixture/mcp",
            workflow_jwt="fixture-workflow-jwt",
        )
        self.scenario = scenario
        self.session_id = session_id
        self.target_analysis_date = target_analysis_date
        self.collect_logs_overrides = collect_logs_overrides or {}
        self.tool_result_overrides = tool_result_overrides or {}
        self.calls: list[str] = []
        self.called_tool_names: list[str] = []

    async def get_workflow_bundle(self) -> WorkflowBootstrap:
        self.calls.append("get_workflow_bundle")
        return self.load_workflow_bootstrap_fixture()

    async def read_resource(self, uri: str) -> str:
        self.calls.append(f"read_resource:{uri}")
        skill_body_by_uri: dict[str, str] = {
            "skill://workflow/normal_patterns": "Normal patterns skill body.",
            "skill://workflow/application_monitoring": "Application monitoring skill body.",
            "skill://workflow/severity_guide": "Severity guide skill body.",
            "skill://workflow/recommendations_guide": "Recommendations guide skill body.",
            "skill://workflow/bot_detection": "Bot detection skill body.",
            "skill://workflow/owasp_security": "OWASP security skill body.",
        }
        return skill_body_by_uri[uri]

    async def list_projects(self) -> list[ProjectManifestSummary]:
        self.calls.append(McpToolName.LIST_PROJECTS)
        return self.load_project_manifest_fixture()

    async def collect_logs(
        self,
        *,
        since: str,
        until: str,
    ) -> CollectLogsArtifact:
        self.calls.append(f"collect_logs:{since}:{until}")
        return self.load_collect_logs_fixture(
            since=since,
            until=until,
            session_id=self.session_id,
            target_analysis_date=self.target_analysis_date,
            overrides=self.collect_logs_overrides,
        )

    async def call_deterministic_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(f"call_deterministic_tool:{name}:{arguments}")
        self.called_tool_names.append(name)
        result_name: str | None = self._fixture_name_for_tool(name, arguments)
        result: dict[str, Any]
        if name == McpToolName.COLLECT_LOGS:
            return self.load_collect_logs_fixture(
                since=str(arguments.get("since") or ""),
                until=str(arguments.get("until") or ""),
                session_id=str(arguments.get("session_id") or self.session_id or ""),
                target_analysis_date=self.target_analysis_date,
                overrides=self.collect_logs_overrides,
            ).model_dump(mode="json")
        if result_name and (self.fixture_root / self.scenario / f"{result_name}.json").exists():
            result = self.load_fixture_payload(
                self.scenario,
                result_name,
                overrides=self.tool_result_overrides.get(name),
                target_analysis_date=self.target_analysis_date,
            )
        else:
            result = self._generic_tool_result(name, arguments)
        if name == McpToolName.GROUP_ERRORS:
            LogAnalysisGroupedErrorsResult.from_mcp_payload(result)
        return result

    @classmethod
    def load_fixture_payload(
        cls,
        scenario: str,
        name: str,
        *,
        overrides: dict[str, Any] | None = None,
        target_analysis_date: date | None = None,
    ) -> dict[str, Any]:
        """Load one MCP fixture payload and apply narrow recursive overrides."""

        path: Path = cls.fixture_root / scenario / f"{name}.json"
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if target_analysis_date is not None:
            payload = cls._normalize_fixture_dates(
                payload,
                target_analysis_date=target_analysis_date,
            )
        if overrides:
            cls._deep_update(payload, overrides)
        return payload

    @classmethod
    def load_workflow_bootstrap_fixture(
        cls,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> WorkflowBootstrap:
        """Load and validate the shared MCP workflow-bootstrap fixture."""

        payload: dict[str, Any] = cls.load_fixture_payload(
            "common",
            "workflow_bootstrap",
            overrides=overrides,
        )
        return WorkflowBootstrap.model_validate(payload)

    @classmethod
    def load_collect_logs_fixture(
        cls,
        *,
        since: str,
        until: str,
        session_id: str | None = None,
        target_analysis_date: date | None = None,
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
            cls._deep_update(payload_overrides, overrides)
        payload: dict[str, Any] = cls.load_fixture_payload(
            "common",
            "collect_logs_base",
            overrides=payload_overrides,
            target_analysis_date=target_analysis_date,
        )
        return CollectLogsArtifact.model_validate(payload)

    @classmethod
    def load_project_manifest_fixture(
        cls,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> list[ProjectManifestSummary]:
        """Load and validate the shared list_projects fixture."""

        payload: dict[str, Any] = cls.load_fixture_payload(
            "common",
            "list_projects",
            overrides=overrides,
        )
        projects: object = payload["result"]
        if not isinstance(projects, list):
            raise TypeError("list_projects fixture must contain a list result.")
        return [ProjectManifestSummary.model_validate(project) for project in projects]

    @staticmethod
    def _fixture_name_for_tool(name: str, arguments: dict[str, Any]) -> str | None:
        if name == McpToolName.GROUP_ERRORS:
            if arguments.get("project_name") == "vps-security":
                return "group_errors_vps_security"
            return "group_errors"
        if name == McpToolName.INSPECT_PROXY_ACTIVITY:
            return "inspect_proxy_activity"
        if name == McpToolName.BUILD_INCIDENT_BUNDLE:
            return "incident_bundle"
        if name == McpToolName.GREP_LOG_SNAPSHOT:
            grep_pattern: str = str(arguments.get("grep") or arguments.get("pattern") or "")
            if ".env" in grep_pattern:
                return "grep_snapshot_env_200"
            return "grep_snapshot"
        production_shape_tools_without_static_fixture = {
            "create_filtered_view",
            "suggest_followup_window",
            "list_log_snapshot_files",
            "read_log_snapshot_file",
            "inspect_live_fail2ban_activity",
            "get_mcp_service_status",
            "get_mcp_health_check",
            McpToolName.LIST_PROJECTS,
        }
        if name in production_shape_tools_without_static_fixture:
            return None
        raise KeyError(f"No fixture is registered for MCP tool {name!r}.")

    @staticmethod
    def _generic_tool_result(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": name,
            "fixture_result": True,
            "summary": (
                "Generic fixture response for a production-advertised MCP tool without "
                "scenario-specific static evidence."
            ),
            "arguments": arguments,
        }

    @classmethod
    def _normalize_fixture_dates(
        cls,
        payload: dict[str, Any],
        *,
        target_analysis_date: date,
    ) -> dict[str, Any]:
        normalized: Any = cls._replace_fixture_dates(
            copy.deepcopy(payload),
            target_analysis_date,
        )
        if not isinstance(normalized, dict):
            raise TypeError("fixture payload must remain a dict after date normalization")
        return normalized

    @classmethod
    def _replace_fixture_dates(cls, value: Any, target_analysis_date: date) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._replace_fixture_dates(item, target_analysis_date)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._replace_fixture_dates(item, target_analysis_date) for item in value]
        if isinstance(value, str):
            return cls._replace_fixture_date_text(value, target_analysis_date)
        return value

    @classmethod
    def _replace_fixture_date_text(cls, text: str, target_analysis_date: date) -> str:
        updated = text
        for offset in range(-3, 4):
            fixture_date = cls.fixture_reference_date + timedelta(days=offset)
            run_date = target_analysis_date + timedelta(days=offset)
            updated = updated.replace(fixture_date.isoformat(), run_date.isoformat())
            updated = updated.replace(
                fixture_date.strftime("%d/%b/%Y"),
                run_date.strftime("%d/%b/%Y"),
            )
        return updated

    @classmethod
    def _deep_update(cls, target: dict[str, Any], updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                cls._deep_update(target[key], value)
            elif (
                isinstance(value, list)
                and isinstance(target.get(key), list)
                and cls._can_merge_list(target[key], value)
            ):
                for index, item in enumerate(value):
                    if isinstance(item, dict) and isinstance(target[key][index], dict):
                        cls._deep_update(target[key][index], item)
                    else:
                        target[key][index] = copy.deepcopy(item)
            else:
                target[key] = copy.deepcopy(value)

    @staticmethod
    def _can_merge_list(target: list[Any], updates: list[Any]) -> bool:
        return len(updates) <= len(target)
