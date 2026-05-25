import json
from datetime import UTC, date, datetime
from typing import cast

import pytest
from llm_core.providers.mock import MockProvider
from llm_core.types import ResponseFormat, TextPart
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

PRIVATE_MONITORING_CONTEXT = (
    "# Private VPS Monitoring Context\n\n"
    "Installed services: landingpage, vps-security, mcp-log-server."
)


class FakeMcpWorkflowClient(McpWorkflowClient):
    def __init__(self) -> None:
        super().__init__(
            base_url="http://mcp.test/mcp",
            workflow_jwt="test-workflow-jwt",
        )
        self.calls: list[str] = []
        self.tool_results: dict[str, dict[str, object]] = {
            "group_errors": {
                "action": "group_errors",
                "project_name": "landingpage",
                "groups": [
                    {
                        "message": "No repeated errors detected",
                        "count": 0,
                    }
                ],
            }
        }

    async def get_workflow_bundle(self) -> WorkflowBootstrap:
        self.calls.append("get_workflow_bundle")
        return WorkflowBootstrap(
            workflow_name="analyze_daily_log_bundle",
            prompt=(
                "# Monitoring Tool Loop System Prompt\n\n"
                "valid top-level actions are call_tools, read_skills, and final_report\n\n"
                "# Log Summary Instructions"
            ),
            mandatory_skills=[
                WorkflowSkill(
                    skill_name="severity_guide",
                    resource_uri="skill://workflow/severity_guide",
                    description="Severity rules for monitored systems.",
                )
            ],
            optional_skills=[
                WorkflowSkill(
                    skill_name="bot_detection",
                    resource_uri="skill://workflow/bot_detection",
                    description="Bot detection guidance.",
                )
            ],
            tools=[
                WorkflowTool(
                    tool_name="group_errors",
                    description="Group repeated errors.",
                    arguments=[
                        {
                            "name": "project_name",
                            "type": "str",
                            "required": True,
                            "default": None,
                        }
                    ],
                )
            ],
        )

    async def read_resource(self, uri: str) -> str:
        self.calls.append(f"read_resource:{uri}")
        if uri == "skill://workflow/bot_detection":
            return "Bot detection skill body."
        return "Severity guide skill body."

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

    async def call_deterministic_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        """Mirror the MCP client boundary used by the LLM tool loop in tests."""

        self.calls.append(f"call_deterministic_tool:{name}:{arguments}")
        return self.tool_results[name]


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
                "action": "call_tools",
                "tool_calls": [
                    {
                        "tool_name": "group_errors",
                        "arguments": {"project_name": "landingpage"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Logs are mostly healthy with one unavailable source.",
                "severity": "WARNING",
                "severity_rationale": "WARNING because one source was unavailable.",
                "key_findings": ["nginx stderr was unavailable"],
                "evidence": ["group_errors found one unavailable source"],
                "coverage_gaps": ["nginx stderr source was unavailable"],
                "recommendations": "Check the nginx stderr source mapping.",
                "watch_only_items": ["Routine bot traffic without service impact"],
                "trend_summary": "No historical trend was available.",
            }
        ),
        usage=Usage(prompt_tokens=100, completion_tokens=40, total_tokens=140, cost_usd=0.01),
    )
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

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
        "read_resource:skill://workflow/severity_guide",
        "list_projects",
        "collect_logs:2026-05-19T00:00:00Z:2026-05-20T00:00:00Z",
        "call_deterministic_tool:group_errors:{'project_name': 'landingpage'}",
    ]
    assert context.workflow.workflow_name == "analyze_daily_log_bundle"
    assert context.collect_logs.projects[0].resolved_source_keys == ["backend", "nginx"]
    assert "Monitoring Tool Loop System Prompt" in context.prompt.system_prompt
    assert "valid top-level actions are call_tools, read_skills" in context.prompt.system_prompt
    assert "Log Summary Instructions" in context.prompt.system_prompt
    assert "Severity guide skill body." in context.prompt.system_prompt
    assert "Private VPS Monitoring Context" in context.prompt.system_prompt
    assert "Installed services: landingpage, vps-security, mcp-log-server." in (
        context.prompt.system_prompt
    )
    assert "Resource: skill://workflow/severity_guide" not in context.prompt.system_prompt
    assert "Severity rules for monitored systems." not in context.prompt.system_prompt
    assert context.prompt.context.analysis_date == date(2026, 5, 19)
    assert [project.project_name for project in context.prompt.context.available_projects] == [
        "landingpage",
        "shop",
    ]
    assert context.prompt.context.collection.projects[0].snapshot_dir == (
        "workflow/landingpage/latest"
    )
    assert context.prompt.context.collection.projects[0].sources[0].source_key == "backend"
    assert context.prompt.context.available_tools[0].tool_name == "group_errors"
    assert len(context.tool_results) == 1
    assert context.tool_results[0].tool_name == "group_errors"
    assert context.tool_results[0].structured_content["action"] == "group_errors"
    assert context.final_report.summary == "Logs are mostly healthy with one unavailable source."
    assert context.final_report.severity == "WARNING"
    assert context.final_report.severity_rationale == (
        "WARNING because one source was unavailable."
    )
    assert context.final_report.evidence == ["group_errors found one unavailable source"]
    assert context.final_report.coverage_gaps == ["nginx stderr source was unavailable"]
    assert context.final_report.watch_only_items == ["Routine bot traffic without service impact"]
    assert context.llm_tokens_used == 140
    assert context.llm_cost_usd == 0.01
    assert len(llm_provider.requests) == 2
    llm_request = llm_provider.requests[0]
    assert llm_request.options.response_format is ResponseFormat.JSON_OBJECT
    assert llm_request.metadata["workflow_name"] == "analyze_daily_log_bundle"
    assert llm_request.messages[0].role == "system"
    assert llm_request.messages[1].role == "user"
    followup_request = llm_provider.requests[1]
    assert followup_request.messages[-1].role == "user"
    assert [message.role for message in followup_request.messages] == ["system", "user", "user"]
    followup_text: str = cast(TextPart, followup_request.messages[-1].parts[0]).text
    assert "previous_action" in followup_text
    assert "tool_results" in followup_text
    assert "group_errors" in followup_text
    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["analysis_date"] == "2026-05-19"
    assert user_prompt["current_phase"] == "inspect_collected_logs"
    assert user_prompt["final_report_allowed"] is False
    assert user_prompt["allowed_actions"] == ["call_tools", "read_skills", "final_report"]
    assert user_prompt["next_required_action"] == "call_tools"
    assert user_prompt["completed_steps"] == [
        "analyze_daily_log_bundle",
        "read_mandatory_skills",
        "list_projects",
        "collect_logs",
    ]
    assert user_prompt["available_projects"][0]["project_name"] == "landingpage"
    assert "Private VPS Monitoring Context" not in context.prompt.user_prompt
    assert user_prompt["mandatory_skills"][0]["name"] == "severity_guide"
    assert user_prompt["mandatory_skills"][0]["resource_uri"] == ("skill://workflow/severity_guide")
    assert user_prompt["collection"]["projects"][0]["snapshot_dir"] == (
        "workflow/landingpage/latest"
    )
    assert user_prompt["snapshot_access"]["workspace"] == "workflow"
    assert user_prompt["snapshot_access"]["session_id_is_for_session_workspace_only"] is True
    assert user_prompt["snapshot_access"]["workflow_followup_arguments"] == [
        "project_name",
        "archive_name",
    ]
    instructions = user_prompt["instructions"]
    assert isinstance(instructions, list)
    assert instructions
    assert all(isinstance(instruction, str) for instruction in instructions)
    assert set(user_prompt["report_contract"]) == {
        "summary",
        "severity",
        "severity_rationale",
        "key_findings",
        "evidence",
        "coverage_gaps",
        "recommendations",
        "watch_only_items",
        "trend_summary",
    }
    assert all(user_prompt["report_contract"].values())
    assert "analysis_date:" not in context.prompt.user_prompt


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_skips_duplicate_mcp_tool_calls(
    mocker: MockerFixture,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    duplicate_action = {
        "action": "call_tools",
        "tool_calls": [
            {
                "tool_name": "group_errors",
                "arguments": {"project_name": "landingpage"},
            }
        ],
    }
    llm_provider.queue_text_response(json.dumps(duplicate_action))
    llm_provider.queue_text_response(json.dumps(duplicate_action))
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Logs were summarized after duplicate tool request was skipped.",
                "severity": "INFO",
                "severity_rationale": "INFO because no service-impacting issue was found.",
                "key_findings": ["Duplicate MCP tool request was skipped."],
                "evidence": ["group_errors result was already available."],
                "coverage_gaps": [],
                "recommendations": "Keep monitoring.",
                "watch_only_items": [],
                "trend_summary": "No historical trend was available.",
            }
        )
    )
    info_mock = mocker.patch("agents.logger.info")
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

    context = await agent.run_log_analysis(
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
        "read_resource:skill://workflow/severity_guide",
        "list_projects",
        "collect_logs:2026-05-19T00:00:00Z:2026-05-20T00:00:00Z",
        "call_deterministic_tool:group_errors:{'project_name': 'landingpage'}",
    ]
    assert [result.tool_name for result in context.tool_results] == [
        "group_errors",
        "duplicate_mcp_tool_call_skipped",
    ]
    duplicate_result = context.tool_results[1]
    assert duplicate_result.structured_content == {
        "action": "duplicate_mcp_tool_call_skipped",
        "tool_name": "group_errors",
        "message": (
            "This MCP tool call was already executed with the same arguments. "
            "Use the previous result, request a different tool, or return final_report."
        ),
    }
    followup_text: str = cast(TextPart, llm_provider.requests[2].messages[-1].parts[0]).text
    assert "duplicate_mcp_tool_call_skipped" in followup_text
    duplicate_log_calls = [
        call
        for call in info_mock.call_args_list
        if call.args and call.args[0] == "skipping duplicate LLM-requested MCP tool call"
    ]
    assert len(duplicate_log_calls) == 1
    assert duplicate_log_calls[0].kwargs["extra"] == {
        "event": "log_analysis_duplicate_mcp_tool_call_skipped",
        "tool_name": "group_errors",
    }


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_logs_llm_actions(
    mocker: MockerFixture,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "call_tools",
                "tool_calls": [
                    {
                        "tool_name": "group_errors",
                        "arguments": {"project_name": "landingpage"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Logs were summarized.",
                "severity": "INFO",
                "severity_rationale": "INFO because no service-impacting issue was found.",
                "key_findings": ["No critical incidents found."],
                "evidence": ["group_errors found no repeated errors."],
                "coverage_gaps": [],
                "recommendations": "Keep monitoring.",
                "watch_only_items": ["Routine bot traffic."],
                "trend_summary": "No trend data available.",
            }
        )
    )
    info_mock = mocker.patch("agents.logger.info")
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

    await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    action_log_calls = [
        call
        for call in info_mock.call_args_list
        if call.args and call.args[0] == "received LLM workflow action"
    ]
    assert len(action_log_calls) == 2
    first_extra = action_log_calls[0].kwargs["extra"]
    assert first_extra["event"] == "log_analysis_llm_action_received"
    assert first_extra["iteration"] == 1
    assert first_extra["action"] == "call_tools"
    assert first_extra["requested_tool_names"] == ["group_errors"]
    assert first_extra["tool_call_count"] == 1
    assert first_extra["llm_response_text"] == (
        '{"action": "call_tools", "tool_calls": [{"tool_name": "group_errors", '
        '"arguments": {"project_name": "landingpage"}}]}'
    )
    assert first_extra["llm_response_structured_output"] is None
    assert first_extra["llm_action_payload"]["tool_calls"][0]["arguments"] == {
        "project_name": "landingpage"
    }
    second_extra = action_log_calls[1].kwargs["extra"]
    assert second_extra["iteration"] == 2
    assert second_extra["action"] == "final_report"
    assert second_extra["final_report_severity"] == "INFO"
    assert second_extra["final_report_key_finding_count"] == 1
    assert '"action": "final_report"' in second_extra["llm_response_text"]
    assert second_extra["llm_response_structured_output"] is None


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_reads_optional_skills() -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "read_skills",
                "skill_names": ["bot_detection"],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Logs were summarized with bot guidance.",
                "severity": "INFO",
                "severity_rationale": "INFO because no service-impacting issue was found.",
                "key_findings": ["No critical incidents found."],
                "evidence": ["bot_detection guidance was reviewed."],
                "coverage_gaps": [],
                "recommendations": "Keep monitoring.",
                "watch_only_items": ["Routine bot traffic."],
                "trend_summary": "No trend data available.",
            }
        )
    )
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

    context: LogAnalysisAgentContext = await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    assert "read_resource:skill://workflow/bot_detection" in mcp_client.calls
    assert len(context.tool_results) == 1
    assert context.tool_results[0].tool_name == "read_skills"
    assert context.tool_results[0].structured_content["skills"][0]["skill_name"] == (
        "bot_detection"
    )
    followup_text: str = cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    assert "Bot detection skill body." in followup_text


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_rejects_unavailable_skill_reads() -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "read_skills",
                "skill_names": ["severity_guide"],
            }
        )
    )
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

    with pytest.raises(ValueError, match="requested unavailable optional skill"):
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
                "severity_rationale": "INFO because no service-impacting issue was found.",
                "key_findings": ["No critical incidents found."],
                "evidence": ["Initial deterministic collection completed."],
                "coverage_gaps": [],
                "recommendations": "Keep monitoring.",
                "watch_only_items": ["Routine bot traffic."],
                "trend_summary": "No trend data available.",
            }
        )
    )
    mocker.patch("agents.monotonic", side_effect=[50.0, 54.321])
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

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
async def test_monitoring_workflow_agent_rejects_unknown_tool_requests() -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "call_tools",
                "tool_calls": [
                    {
                        "tool_name": "delete_everything",
                        "arguments": {},
                    }
                ],
            }
        )
    )
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

    with pytest.raises(ValueError, match="requested unavailable MCP tool"):
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
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

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
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=MockProvider(),
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )

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
        "read_resource:skill://workflow/severity_guide",
        "list_projects",
    ]
