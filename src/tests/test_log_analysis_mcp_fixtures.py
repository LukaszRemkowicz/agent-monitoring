import json
from datetime import UTC, date, datetime

import pytest
from llm_core.providers.mock import MockProvider

from schemas import (
    LogAnalysisAgentContext,
    LogAnalysisAllowedAction,
    LogAnalysisSeverity,
    LogCollectionWindow,
    McpToolName,
    WorkflowBootstrap,
)
from tests.conftest import AgentFactory
from tests.log_analysis_mcp_fixtures import (
    FixtureBackedMcpWorkflowClient,
    load_log_analysis_mcp_fixture,
    load_project_manifest_fixture,
    load_workflow_bootstrap_fixture,
)


def test_log_analysis_mcp_fixtures_validate_common_workflow_payload() -> None:
    workflow: WorkflowBootstrap = load_workflow_bootstrap_fixture()
    collect_logs: dict[str, object] = load_log_analysis_mcp_fixture(
        "common",
        "collect_logs_base",
    )

    assert workflow.workflow_name == McpToolName.ANALYZE_DAILY_LOG_BUNDLE
    assert collect_logs["action"] == McpToolName.COLLECT_LOGS
    assert [tool.tool_name for tool in workflow.tools] == [
        McpToolName.GROUP_ERRORS,
        McpToolName.INSPECT_PROXY_ACTIVITY,
        McpToolName.GREP_LOG_SNAPSHOT,
        McpToolName.BUILD_INCIDENT_BUNDLE,
    ]
    projects = load_project_manifest_fixture()
    assert [project.project_name for project in projects] == ["landingpage", "vps-security"]
    assert projects[0].source_keys == [
        "nginx",
        "traefik",
        "backend",
        "frontend",
        "celery_worker",
        "celery_beat",
    ]
    assert projects[1].source_keys == ["fail2ban", "nginx_access", "traefik_access"]
    assert collect_logs["requested_project_names"] == ["landingpage", "vps-security"]


@pytest.mark.parametrize("scenario", ["sensitive_path_success", "backend_5xx"])
def test_log_analysis_group_error_fixtures_include_signal_and_noise(scenario: str) -> None:
    landingpage_group_errors = load_log_analysis_mcp_fixture(scenario, "group_errors")
    vps_security_group_errors = load_log_analysis_mcp_fixture(
        scenario,
        "group_errors_vps_security",
    )

    landingpage_groups = landingpage_group_errors["groups"]
    vps_security_groups = vps_security_group_errors["groups"]

    assert isinstance(landingpage_groups, list)
    assert isinstance(vps_security_groups, list)
    assert len(landingpage_groups) >= 3
    assert len(vps_security_groups) >= 2
    assert len(landingpage_group_errors["searched_source_keys"]) >= 4
    assert set(vps_security_group_errors["searched_source_keys"]) == {
        "fail2ban",
        "nginx_access",
        "traefik_access",
    }
    assert any(group["severity"] in {"critical", "high"} for group in landingpage_groups)
    assert any(group["severity"] in {"low", "medium"} for group in landingpage_groups)
    assert any(len(group["source_keys"]) > 1 for group in landingpage_groups)
    assert all("first_seen" in group and "last_seen" in group for group in landingpage_groups)


@pytest.mark.asyncio
async def test_fixture_backed_mcp_client_runs_real_agent_loop(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FixtureBackedMcpWorkflowClient(
        scenario="sensitive_path_success",
        session_id="phase-9a-session",
    )
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GREP_LOG_SNAPSHOT,
                        "arguments": {
                            "project_name": "landingpage",
                            "source_key": "nginx",
                            "pattern": "/.env",
                        },
                    },
                    {
                        "tool_name": McpToolName.INSPECT_PROXY_ACTIVITY,
                        "arguments": {"project_name": "landingpage"},
                    },
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Sensitive path access was investigated with current MCP facts.",
                "severity": LogAnalysisSeverity.CRITICAL,
                "severity_rationale": "CRITICAL because current logs include /.env returning 200.",
                "key_findings": ["A sensitive path returned HTTP 200 in current logs."],
                "evidence": [
                    "group_errors identified the current sensitive-path family.",
                    "grep_log_snapshot confirmed /.env returned 200.",
                    "inspect_proxy_activity showed current proxy status context.",
                ],
                "coverage_gaps": [],
                "recommendations": "Rotate any exposed secrets and block the sensitive path.",
                "watch_only_items": [],
                "trend_summary": "This is current high-severity evidence, not history-only noise.",
            }
        )
    )
    agent = agent_factory(mcp_client, llm_provider)

    context: LogAnalysisAgentContext = await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
        historical_context="",
    )

    assert context.collect_logs.session_id == "phase-9a-session"
    assert context.final_report.severity == LogAnalysisSeverity.CRITICAL
    assert [result.tool_name for result in context.tool_results] == [
        McpToolName.GREP_LOG_SNAPSHOT,
        McpToolName.INSPECT_PROXY_ACTIVITY,
    ]
    assert mcp_client.called_tool_names == [
        McpToolName.GROUP_ERRORS,
        McpToolName.GROUP_ERRORS,
        McpToolName.GREP_LOG_SNAPSHOT,
        McpToolName.INSPECT_PROXY_ACTIVITY,
    ]
