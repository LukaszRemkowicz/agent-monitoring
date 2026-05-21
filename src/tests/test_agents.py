import json
from datetime import UTC, date, datetime

import pytest
from llm_core.providers.mock import MockProvider
from llm_core.types import ResponseFormat
from llm_core.usage import Usage
from pytest_mock import MockerFixture

from agents import MonitoringWorkflowAgent
from exceptions import McpClientError
from mcp import McpWorkflowClient
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogCollectionWindow,
    ProjectManifestSummary,
    WorkflowBootstrap,
    WorkflowSkill,
    WorkflowTool,
)
from tests.conftest import build_collect_logs_artifact_payload


class FakeMcpWorkflowClient(McpWorkflowClient):
    def __init__(self) -> None:
        super().__init__(
            base_url="http://mcp.test/mcp",
            workflow_jwt="test-workflow-jwt",
        )
        self.calls: list[str] = []

    async def get_workflow_bundle(self) -> WorkflowBootstrap:
        self.calls.append("get_workflow_bundle")
        return WorkflowBootstrap(
            workflow_name="analyze_daily_log_bundle",
            prompt=(
                "# Monitoring Tool Loop System Prompt\n\n"
                "valid top-level actions are only call_tools and final_report\n\n"
                "# Log Summary Instructions"
            ),
            mandatory_skills=[
                WorkflowSkill(
                    skill_name="project_context",
                    resource_uri="skill://workflow/project_context",
                    description="Project context for monitored systems.",
                )
            ],
            optional_skills=[],
            tools=[
                WorkflowTool(
                    tool_name="group_snapshot_errors",
                    description="Group repeated errors.",
                )
            ],
        )

    async def read_resource(self, uri: str) -> str:
        self.calls.append(f"read_resource:{uri}")
        return "Project context skill body."

    async def collect_logs(
        self,
        *,
        since: str,
        until: str,
    ) -> CollectLogsArtifact:
        self.calls.append(f"collect_logs:{since}:{until}")
        return CollectLogsArtifact.model_validate(
            build_collect_logs_artifact_payload(
                since=since,
                until=until,
                session_id="generated-workflow-session-id",
                requested_project_names=["landingpage", "shop"],
                next_step_tips=["Use group_snapshot_errors before final report."],
                warnings=["nginx stderr unavailable"],
                include_unavailable_nginx=True,
            )
        )

    async def list_projects(self) -> list[ProjectManifestSummary]:
        self.calls.append("list_projects")
        return [
            ProjectManifestSummary(
                project_name="landingpage",
                project_summary="Landingpage project.",
                source_keys=["backend", "nginx"],
            ),
            ProjectManifestSummary(
                project_name="shop",
                project_summary="Shop project.",
                source_keys=["backend"],
            ),
        ]


class FakeMcpWorkflowClientWithoutProjects(FakeMcpWorkflowClient):
    async def list_projects(self) -> list[ProjectManifestSummary]:
        self.calls.append("list_projects")
        return []


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_collects_logs_and_prepares_prompt_context() -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Logs are mostly healthy with one unavailable source.",
                "severity": "WARNING",
                "key_findings": ["nginx stderr was unavailable"],
                "recommendations": "Check the nginx stderr source mapping.",
                "trend_summary": "No historical trend was available.",
            }
        ),
        usage=Usage(prompt_tokens=100, completion_tokens=40, total_tokens=140, cost_usd=0.01),
    )
    agent = MonitoringWorkflowAgent(mcp_client, llm_provider=llm_provider)

    context: LogAnalysisAgentContext = await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    assert mcp_client.calls == [
        "get_workflow_bundle",
        "read_resource:skill://workflow/project_context",
        "list_projects",
        "collect_logs:2026-05-19T00:00:00Z:2026-05-20T00:00:00Z",
    ]
    assert context.workflow.workflow_name == "analyze_daily_log_bundle"
    assert context.collect_logs.projects[0].resolved_source_keys == ["backend", "nginx"]
    assert "Monitoring Tool Loop System Prompt" in context.prompt.system_prompt
    assert "valid top-level actions are only" in context.prompt.system_prompt
    assert "Log Summary Instructions" in context.prompt.system_prompt
    assert "Project context skill body." in context.prompt.system_prompt
    assert "Resource: skill://workflow/project_context" not in context.prompt.system_prompt
    assert "Project context for monitored systems." not in context.prompt.system_prompt
    assert context.prompt.context.analysis_date == date(2026, 5, 19)
    assert [project.project_name for project in context.prompt.context.available_projects] == [
        "landingpage",
        "shop",
    ]
    assert context.prompt.context.collection.projects[0].snapshot_dir == (
        "workflow/landingpage/latest"
    )
    assert context.prompt.context.collection.projects[0].sources[0].source_key == "backend"
    assert context.prompt.context.available_tools[0].tool_name == "group_snapshot_errors"
    assert context.final_report.summary == "Logs are mostly healthy with one unavailable source."
    assert context.final_report.severity == "WARNING"
    assert context.llm_tokens_used == 140
    assert context.llm_cost_usd == 0.01
    assert len(llm_provider.requests) == 1
    llm_request = llm_provider.requests[0]
    assert llm_request.options.response_format is ResponseFormat.JSON_OBJECT
    assert llm_request.metadata["workflow_name"] == "analyze_daily_log_bundle"
    assert llm_request.messages[0].role == "system"
    assert llm_request.messages[1].role == "user"
    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["analysis_date"] == "2026-05-19"
    assert user_prompt["current_phase"] == "final_report"
    assert user_prompt["final_report_allowed"] is True
    assert user_prompt["next_required_action"] == "final_report"
    assert user_prompt["completed_steps"] == [
        "analyze_daily_log_bundle",
        "read_mandatory_skills",
        "list_projects",
        "collect_logs",
    ]
    assert user_prompt["available_projects"][0]["project_name"] == "landingpage"
    assert user_prompt["mandatory_skills"][0]["name"] == "project_context"
    assert user_prompt["mandatory_skills"][0]["resource_uri"] == (
        "skill://workflow/project_context"
    )
    assert user_prompt["collection"]["projects"][0]["snapshot_dir"] == (
        "workflow/landingpage/latest"
    )
    assert user_prompt["snapshot_access"]["workspace"] == "workflow"
    assert user_prompt["snapshot_access"]["session_id_is_for_session_workspace_only"] is True
    assert user_prompt["snapshot_access"]["workflow_followup_arguments"] == [
        "project_name",
        "archive_name",
    ]
    assert "Zero collected log lines are not evidence that a service is healthy." in (
        user_prompt["instructions"]
    )
    assert "Use source_key names exactly as MCP reports them." in user_prompt["instructions"]
    assert "analysis_date:" not in context.prompt.user_prompt


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_records_llm_report_time(
    mocker: MockerFixture,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Logs were summarized.",
                "severity": "INFO",
                "key_findings": ["No critical incidents found."],
                "recommendations": "Keep monitoring.",
                "trend_summary": "No trend data available.",
            }
        )
    )
    mocker.patch("agents.monotonic", side_effect=[50.0, 54.321])
    agent = MonitoringWorkflowAgent(mcp_client, llm_provider=llm_provider)

    context: LogAnalysisAgentContext = await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    assert context.llm_report_execution_time_seconds == 4.321


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_rejects_invalid_final_report() -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Missing required fields.",
                "severity": "NOTICE",
            }
        )
    )
    agent = MonitoringWorkflowAgent(mcp_client, llm_provider=llm_provider)

    with pytest.raises(ValueError, match="LLM final report did not match expected shape"):
        await agent.run_log_analysis(
            analysis_date=date(2026, 5, 19),
            log_window=LogCollectionWindow(
                since="2026-05-19T00:00:00Z",
                until="2026-05-20T00:00:00Z",
                since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
                until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
            ),
        )


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_stops_when_mcp_has_no_projects() -> None:
    mcp_client = FakeMcpWorkflowClientWithoutProjects()
    agent = MonitoringWorkflowAgent(mcp_client, llm_provider=MockProvider())

    with pytest.raises(McpClientError) as error_info:
        await agent.run_log_analysis(
            analysis_date=date(2026, 5, 19),
            log_window=LogCollectionWindow(
                since="2026-05-19T00:00:00Z",
                until="2026-05-20T00:00:00Z",
                since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
                until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
            ),
        )

    assert "returned no projects" in str(error_info.value)
    assert "Upload project manifests to MCP" in str(error_info.value)
    assert "caller project scope" in str(error_info.value)
    assert error_info.value.tool_name == "list_projects"
    assert mcp_client.calls == [
        "get_workflow_bundle",
        "read_resource:skill://workflow/project_context",
        "list_projects",
    ]
