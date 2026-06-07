import json
from datetime import UTC, date, datetime

import pytest
from llm_core.providers.mock import MockProvider

from devtools.mcp import FakerMCP
from schemas import (
    LogAnalysisAgentContext,
    LogAnalysisAllowedAction,
    LogAnalysisSeverity,
    LogCollectionWindow,
    McpToolName,
    WorkflowBootstrap,
)
from tests.conftest import AgentFactory


def test_log_analysis_mcp_fixtures_validate_common_workflow_payload() -> None:
    workflow: WorkflowBootstrap = FakerMCP.load_workflow_bootstrap_fixture()
    collect_logs: dict[str, object] = FakerMCP.load_fixture_payload(
        "common",
        "collect_logs_base",
    )

    assert workflow.workflow_name == McpToolName.ANALYZE_DAILY_LOG_BUNDLE
    assert collect_logs["action"] == McpToolName.COLLECT_LOGS
    assert [tool.tool_name for tool in workflow.tools] == [
        McpToolName.GROUP_ERRORS,
        McpToolName.INSPECT_PROXY_ACTIVITY,
        McpToolName.BUILD_INCIDENT_BUNDLE,
        "create_filtered_view",
        "suggest_followup_window",
        McpToolName.LIST_PROJECTS,
        McpToolName.COLLECT_LOGS,
        "inspect_live_fail2ban_activity",
        "list_log_snapshot_files",
        "read_log_snapshot_file",
        McpToolName.GREP_LOG_SNAPSHOT,
        "get_mcp_service_status",
        "get_mcp_health_check",
    ]
    assert [skill.name for skill in workflow.mandatory_skills] == [
        "normal_patterns",
        "application_monitoring",
        "severity_guide",
        "recommendations_guide",
    ]
    projects = FakerMCP.load_project_manifest_fixture()
    assert [project.project_name for project in projects] == ["demo-shop", "host-security"]
    assert projects[0].source_keys == [
        "nginx",
        "traefik",
        "backend",
        "frontend",
        "worker",
        "scheduler",
    ]
    assert projects[1].source_keys == ["fail2ban", "edge_access", "proxy_access"]
    assert collect_logs["requested_project_names"] == ["demo-shop", "host-security"]


@pytest.mark.parametrize("scenario", ["sensitive_path_success", "backend_5xx"])
def test_log_analysis_group_error_fixtures_include_signal_and_noise(scenario: str) -> None:
    demo_shop_group_errors = FakerMCP.load_fixture_payload(
        scenario,
        "group_errors",
    )
    host_security_group_errors = FakerMCP.load_fixture_payload(
        scenario,
        "group_errors_host_security",
    )

    demo_shop_groups = demo_shop_group_errors["groups"]
    host_security_groups = host_security_group_errors["groups"]

    assert isinstance(demo_shop_groups, list)
    assert isinstance(host_security_groups, list)
    assert len(demo_shop_groups) >= 3
    assert len(host_security_groups) >= 2
    assert len(demo_shop_group_errors["searched_source_keys"]) >= 4
    assert set(host_security_group_errors["searched_source_keys"]) == {
        "fail2ban",
        "edge_access",
        "proxy_access",
    }
    assert any(group["severity"] in {"critical", "high"} for group in demo_shop_groups)
    assert any(group["severity"] in {"low", "medium"} for group in demo_shop_groups)
    assert any(len(group["source_keys"]) > 1 for group in demo_shop_groups)
    assert all("first_seen" in group and "last_seen" in group for group in demo_shop_groups)


@pytest.mark.asyncio
async def test_fixture_backed_mcp_client_runs_real_agent_loop(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakerMCP(
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
                            "project_name": "demo-shop",
                            "source_key": "nginx",
                            "pattern": "/.env",
                        },
                    },
                    {
                        "tool_name": McpToolName.INSPECT_PROXY_ACTIVITY,
                        "arguments": {"project_name": "demo-shop"},
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
