import json
from datetime import UTC, date, datetime
from typing import cast

import pytest
from llm_core.providers.mock import MockProvider
from llm_core.types import ResponseFormat, TextPart
from llm_core.usage import Usage
from pytest_mock import MockerFixture

from agents import MonitoringWorkflowAgent
from db.models import LogAnalysisLLMCall
from exceptions import McpClientError
from mcp import McpWorkflowClient
from repositories import LLMCallRepository
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogAnalysisOut,
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
                    when_useful=(
                        "Use for scanner/probe-heavy traffic, clustered 404/405s, "
                        "and suspicious infrastructure warnings."
                    ),
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
                ),
                WorkflowTool(
                    tool_name="inspect_proxy_activity",
                    description="Inspect proxy status distribution.",
                    arguments=[],
                ),
                WorkflowTool(
                    tool_name="inspect_live_fail2ban_activity",
                    description="Inspect live fail2ban jails.",
                    arguments=[],
                ),
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
        historical_context="",
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
    assert "available_tool_inventory" in followup_text
    assert "inspect_live_fail2ban_activity" in followup_text
    assert "optional_skill_inventory" in followup_text
    assert "scanner/probe-heavy traffic" in followup_text
    assert "already_retrieved" in followup_text
    assert "Before returning final_report, compare tool_results" in followup_text
    assert "evidence_mode controls evidence wording" in followup_text
    assert "evidence_mode=metadata_and_previous_analysis_only" in followup_text
    assert "coverage_gaps must describe current_coverage only" in followup_text
    assert "tool_results show bot, scanner, probe" in followup_text
    assert "bot_detection with already_retrieved=false" in followup_text
    assert "unless previous_analysis shows the same known watch-only pattern" in followup_text
    assert "do not automatically read optional skills" in followup_text
    assert "do not automatically call live mitigation tools" in followup_text
    assert "If you skip a tool because previous_analysis is sufficient" in followup_text
    assert "do not cite that skipped tool" in followup_text
    assert "Use previous_analysis or collected snapshot metadata" in followup_text
    assert "available_tool_inventory is an inventory of capabilities, not evidence" in followup_text
    assert "Do not write inspect_proxy_activity results" in followup_text
    assert "unless current tool_results contain those outputs" in followup_text
    assert "When no current tool_results are present" in followup_text
    assert "consistent with previous_analysis" in followup_text
    assert "avoid fresh-detection wording" in followup_text
    assert "Line counts are coverage metadata only" in followup_text
    assert "A line count does not prove status codes" in followup_text
    assert "Forbidden zero-tool current-run claims" in followup_text
    assert "logs show mostly 2xx/3xx" in followup_text
    assert "Use previous_analysis reported" in followup_text
    assert "workflow guidance rather than model memory" in followup_text
    assert "tool_results show possible security impact" in followup_text
    assert "successful sensitive-path access" in followup_text
    assert "auth/admin/API abuse" in followup_text
    assert "injection or path-traversal patterns" in followup_text
    assert "owasp_security with already_retrieved=false" in followup_text
    assert "action=read_skills for owasp_security before final_report" in followup_text
    assert "Treat logs as historical observations" in followup_text
    assert "prefer the tool over inference from logs" in followup_text
    assert "When the available project or tool context includes a host security daemon" in (
        followup_text
    )
    assert "historical security daemon logs are evidence that bans occurred in the past" in (
        followup_text
    )
    assert "use action=call_tools for inspect_live_fail2ban_activity" in followup_text
    assert "so mitigation analysis is based on live evidence rather than hypothesis" in (
        followup_text
    )
    assert "If you return final_report without it" in followup_text
    assert "Do not claim the host security daemon is active, blocking, or effective" in (
        followup_text
    )
    assert "Zero current bans means no IPs are banned at inspection time" in followup_text
    assert "does not by itself indicate past mitigation" in followup_text
    assert "Do not write that zero current bans are consistent with past mitigation" in (
        followup_text
    )
    assert "Write only that no IPs were banned at inspection time" in followup_text
    assert "source emitted no logs in the analysis window" in followup_text
    assert "do not claim it is healthy, broken, unused, or error-free" in followup_text
    assert "scheduled-job activity was not observable from logs" in followup_text
    assert "blocked scanner/probe noise" not in context.prompt.user_prompt
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
    assert user_prompt["optional_skills"][0]["when_useful"].startswith(
        "Use for scanner/probe-heavy traffic"
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
    instructions = user_prompt["instructions"]
    assert isinstance(instructions, list)
    assert instructions
    assert all(isinstance(instruction, str) for instruction in instructions)
    joined_instructions = "\n".join(instructions)
    assert "evidence_mode controls evidence wording" in joined_instructions
    assert "evidence_mode=metadata_and_previous_analysis_only" in joined_instructions
    assert "do not describe current log content beyond collection coverage" in joined_instructions
    assert "If history_comparison.recommended_action=call_tools" in joined_instructions
    assert "call deterministic MCP tools before final_report" in joined_instructions
    assert "previous_analysis severity is WARNING or CRITICAL" in joined_instructions
    assert "do not preserve the previous severity by inertia" in joined_instructions
    assert "scanner/probe 4xx responses or intended 403 access restrictions remain INFO" in (
        joined_instructions
    )
    assert "coverage_gaps must describe current_coverage only" in joined_instructions
    assert "Do not copy previous_analysis coverage_snapshot" in joined_instructions
    assert "history_comparison.changed_sources belong in trend_summary or evidence" in (
        joined_instructions
    )
    assert "previous_analysis shows the same known watch-only pattern" in joined_instructions
    assert "do not automatically read optional skills" in joined_instructions
    assert "do not automatically call live mitigation tools" in joined_instructions
    assert "Optional skills and live mitigation tools are for new, changed, worse" in (
        joined_instructions
    )
    assert "If you skip a tool because previous_analysis is sufficient" in joined_instructions
    assert "do not cite that skipped tool" in joined_instructions
    assert "do not claim live runtime state" in joined_instructions
    assert "Use previous_analysis or collected snapshot metadata" in joined_instructions
    assert "available_tools is an inventory of capabilities, not evidence" in joined_instructions
    assert "Do not write inspect_proxy_activity results" in joined_instructions
    assert "unless current tool_results contain those outputs" in joined_instructions
    assert "When no current tool_results are present" in joined_instructions
    assert "consistent with previous_analysis" in joined_instructions
    assert "avoid fresh-detection wording" in joined_instructions
    assert "detected, found, grouped, active, currently banning" in joined_instructions
    assert "unless current tool_results explicitly prove it" in joined_instructions
    assert "Line counts are coverage metadata only" in joined_instructions
    assert "A line count does not prove status codes" in joined_instructions
    assert "routes, paths, bans, upstream errors" in joined_instructions
    assert "no service impact" in joined_instructions
    assert "If only line counts and previous_analysis are available" in joined_instructions
    assert "Forbidden zero-tool current-run claims" in joined_instructions
    assert "logs show mostly 2xx/3xx" in joined_instructions
    assert "logs show scanner/probe traffic" in joined_instructions
    assert "Fail2ban is active" in joined_instructions
    assert "no 5xx or upstream errors were detected" in joined_instructions
    assert "Use previous_analysis reported" in joined_instructions
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
    assert "current MCP tool results when present" in user_prompt["report_contract"]["evidence"]
    assert "collected snapshot metadata" in user_prompt["report_contract"]["evidence"]
    assert "previous_analysis" in user_prompt["report_contract"]["evidence"]
    assert "analysis_date:" not in context.prompt.user_prompt


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_includes_historical_context_in_system_prompt() -> None:
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
                "summary": "Logs are healthy.",
                "severity": "INFO",
                "severity_rationale": "INFO because no new issue was found.",
                "key_findings": ["No new critical errors."],
                "evidence": ["Current grouped errors match historical noise."],
                "coverage_gaps": [],
                "recommendations": "No immediate action needed.",
                "watch_only_items": ["Known scanner noise."],
                "trend_summary": "Scanner noise remained stable versus 2026-05-18.",
            }
        )
    )
    historical_context = (
        "## 2026-05-18 — Severity: INFO\n"
        "Summary: Previous run saw scanner noise only.\n"
        "Key findings: ['No service impact.']\n"
        "Recommendations: No action needed."
    )
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
        historical_context=historical_context,
    )

    assert "HISTORICAL LOG ANALYSIS (last 5 days from DB)" in context.prompt.system_prompt
    assert historical_context in context.prompt.system_prompt
    assert "YOUR TASK: TEMPORAL COMPARISON" in context.prompt.system_prompt
    assert "Previous run saw scanner noise only." not in context.prompt.user_prompt
    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["historical_context_available"] is True
    assert user_prompt["trend_summary_instruction"] == (
        "Historical context was provided in the system prompt. Compare current "
        "tool results against it and do not claim no historical data was provided."
    )
    followup_text = cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    assert '"historical_context_available": true' in followup_text
    assert "do not claim no historical data was provided" in followup_text


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_includes_previous_analysis_in_user_prompt() -> None:
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
                "summary": "Logs match previous scanner noise.",
                "severity": "INFO",
                "severity_rationale": "INFO because no new service impact was found.",
                "key_findings": ["Known scanner noise persisted."],
                "evidence": ["group_errors matched prior clean backend pattern."],
                "coverage_gaps": [],
                "recommendations": "Continue monitoring.",
                "watch_only_items": ["Routine bot traffic."],
                "trend_summary": "No material change from the previous run.",
            }
        )
    )
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Known scanner noise only.",
        severity="INFO",
        trend_summary="Scanner noise was stable.",
        deterministic_fingerprint={"report": {"severity": "INFO"}},
        evidence_fingerprints=["evidence:abc"],
        known_patterns=[{"pattern": "Routine bot traffic."}],
        coverage_snapshot={
            "totals": {
                "project_count": 1,
                "source_count": 2,
                "zero_line_sources": 1,
            },
            "projects": [
                {
                    "project_name": "landingpage",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": "collected",
                            "line_count": 120,
                            "zero_lines": False,
                        },
                        {
                            "source_key": "nginx",
                            "status": "unavailable",
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ],
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
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
        previous_analysis=previous_analysis,
    )

    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["next_required_action"] == "final_report"
    assert user_prompt["final_report_allowed"] is True
    assert user_prompt["evidence_mode"] == "metadata_and_previous_analysis_only"
    assert user_prompt["current_tool_result_count"] == 0
    assert user_prompt["current_coverage"] == {
        "zero_line_sources": [],
        "unavailable_sources": ["landingpage.nginx"],
    }
    assert user_prompt["history_comparison"] == {
        "available": True,
        "coverage_changed": False,
        "changed_sources": [],
        "recommended_action": "final_report",
        "rationale": "Previous and current source coverage metadata match.",
    }
    assert user_prompt["previous_analysis"] == {
        "analysis_date": "2026-05-18",
        "summary": "Known scanner noise only.",
        "severity": "INFO",
        "trend_summary": "Scanner noise was stable.",
        "deterministic_fingerprint": {"report": {"severity": "INFO"}},
        "evidence_fingerprints": ["evidence:abc"],
        "known_patterns": [{"pattern": "Routine bot traffic."}],
        "coverage_snapshot": {
            "totals": {
                "project_count": 1,
                "source_count": 2,
                "zero_line_sources": 1,
            }
        },
        "fingerprint_version": "log-analysis-fingerprint-v1",
    }
    assert "projects" not in user_prompt["previous_analysis"]["coverage_snapshot"]
    followup_prompt = json.loads(
        cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    )
    assert followup_prompt["previous_analysis"] == user_prompt["previous_analysis"]
    assert followup_prompt["history_comparison"] == user_prompt["history_comparison"]
    assert followup_prompt["current_coverage"] == user_prompt["current_coverage"]
    assert followup_prompt["evidence_mode"] == "current_tool_results_available"
    assert followup_prompt["current_tool_result_count"] == 1


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_flags_changed_history_coverage() -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Coverage changed.",
                "severity": "INFO",
                "severity_rationale": "Coverage changed but no report was escalated.",
                "key_findings": ["Coverage changed."],
                "evidence": ["history_comparison showed changed coverage."],
                "coverage_gaps": [],
                "recommendations": "Inspect changed coverage.",
                "watch_only_items": [],
                "trend_summary": "Coverage changed.",
            }
        )
    )
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Known scanner noise only.",
        severity="INFO",
        deterministic_fingerprint={},
        evidence_fingerprints=[],
        known_patterns=[],
        coverage_snapshot={
            "totals": {
                "project_count": 1,
                "source_count": 2,
                "zero_line_sources": 2,
            },
            "projects": [
                {
                    "project_name": "landingpage",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": "collected",
                            "line_count": 0,
                            "zero_lines": True,
                        },
                        {
                            "source_key": "nginx",
                            "status": "collected",
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ],
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
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
        previous_analysis=previous_analysis,
    )

    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["history_comparison"] == {
        "available": True,
        "coverage_changed": True,
        "changed_sources": ["landingpage.backend"],
        "recommended_action": "call_tools",
        "rationale": (
            "Previous and current source coverage differ; call deterministic tools "
            "before final_report."
        ),
    }
    assert user_prompt["current_coverage"] == {
        "zero_line_sources": [],
        "unavailable_sources": ["landingpage.nginx"],
    }
    assert user_prompt["previous_analysis"]["coverage_snapshot"] == {
        "totals": {
            "project_count": 1,
            "source_count": 2,
            "zero_line_sources": 2,
        }
    }
    assert "projects" not in user_prompt["previous_analysis"]["coverage_snapshot"]
    assert user_prompt["next_required_action"] == "call_tools"
    assert user_prompt["final_report_allowed"] is False
    assert user_prompt["evidence_mode"] == "history_changed_requires_tools"


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_requires_tools_for_previous_warning() -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Previous warning requires tools.",
                "severity": "WARNING",
                "severity_rationale": "Prior warning should be verified.",
                "key_findings": ["Previous run had 500s."],
                "evidence": ["history_comparison required tools."],
                "coverage_gaps": [],
                "recommendations": "Verify with deterministic tools.",
                "watch_only_items": [],
                "trend_summary": "Previous warning persisted.",
            }
        )
    )
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Backend and frontend had HTTP 500 errors.",
        severity="WARNING",
        deterministic_fingerprint={
            "status_signals": {
                "landingpage.backend": {"status_500_count": 7},
                "landingpage.frontend": {"status_500_count": 5},
            }
        },
        evidence_fingerprints=[
            "simulated:landingpage.backend:http_500:count_7",
            "simulated:landingpage.frontend:http_500:count_5",
        ],
        known_patterns=[],
        coverage_snapshot={
            "projects": [
                {
                    "project_name": "landingpage",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": "collected",
                            "line_count": 120,
                            "zero_lines": False,
                        },
                        {
                            "source_key": "nginx",
                            "status": "unavailable",
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ]
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
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
        previous_analysis=previous_analysis,
    )

    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["history_comparison"] == {
        "available": True,
        "coverage_changed": False,
        "changed_sources": [],
        "recommended_action": "call_tools",
        "rationale": (
            "Previous analysis severity was WARNING; call deterministic tools before "
            "final_report to verify whether the prior warning or critical condition "
            "is still present."
        ),
    }
    assert user_prompt["next_required_action"] == "call_tools"
    assert user_prompt["final_report_allowed"] is False
    assert user_prompt["evidence_mode"] == "history_changed_requires_tools"


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
async def test_monitoring_workflow_agent_does_not_add_local_probe_interpretation() -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results.update(
        {
            "inspect_proxy_activity": {
                "action": "inspect_proxy_activity",
                "project_name": "landingpage",
                "total_requests": 100,
                "status_class_counts": {"2xx": 20, "4xx": 80, "5xx": 0},
                "upstream_error_count": 0,
            },
            "inspect_live_fail2ban_activity": {
                "action": "inspect_live_fail2ban_activity",
                "project_name": "vps-security",
                "active_jails": 3,
                "currently_banned_total": 2,
            },
        }
    )
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "call_tools",
                "tool_calls": [
                    {
                        "tool_name": "inspect_proxy_activity",
                        "arguments": {"project_name": "landingpage"},
                    },
                    {
                        "tool_name": "inspect_live_fail2ban_activity",
                        "arguments": {"project_name": "vps-security"},
                    },
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Scanner traffic is blocked.",
                "severity": "INFO",
                "severity_rationale": "INFO because probe noise has no service impact.",
                "key_findings": ["Proxy noise is covered by active fail2ban."],
                "evidence": ["Proxy and fail2ban tool results were reviewed."],
                "coverage_gaps": [],
                "recommendations": "No immediate mitigation change is indicated.",
                "watch_only_items": ["Blocked scanner traffic."],
                "trend_summary": "No trend data available.",
            }
        )
    )
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

    assert [result.tool_name for result in context.tool_results] == [
        "inspect_proxy_activity",
        "inspect_live_fail2ban_activity",
    ]
    assert "monitoring_app_probe_interpretation" not in {
        result.tool_name for result in context.tool_results
    }
    followup_text = cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    assert "monitoring_app_probe_interpretation" not in followup_text
    assert "inspect_proxy_activity" in followup_text
    assert "inspect_live_fail2ban_activity" in followup_text


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_persists_llm_tool_usage_by_trace_id() -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results.update(
        {
            "inspect_proxy_activity": {
                "action": "inspect_proxy_activity",
                "status_class_counts": {"4xx": 3, "5xx": 0},
                "upstream_error_count": 0,
            },
            "inspect_live_fail2ban_activity": {
                "action": "inspect_live_fail2ban_activity",
                "active_jails": 1,
            },
        }
    )
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "call_tools",
                "tool_calls": [
                    {
                        "tool_name": "inspect_proxy_activity",
                        "arguments": {"project_name": "landingpage"},
                    },
                    {
                        "tool_name": "inspect_live_fail2ban_activity",
                        "arguments": {"project_name": "vps-security"},
                    },
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": "final_report",
                "summary": "Scanner traffic is blocked.",
                "severity": "INFO",
                "severity_rationale": "No service impact.",
                "key_findings": ["Probe traffic only."],
                "evidence": ["Proxy and fail2ban tools were used."],
                "coverage_gaps": [],
                "recommendations": "No action needed.",
                "watch_only_items": ["Probe traffic."],
                "trend_summary": "No trend data available.",
            }
        )
    )
    llm_call_repository = LLMCallRepository(trace_id="trace-tool-usage")
    agent = MonitoringWorkflowAgent(
        mcp_client,
        llm_provider=llm_provider,
        private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
    )
    agent.llm_call_repository = llm_call_repository

    await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    steps: list[LogAnalysisLLMCall] = await LogAnalysisLLMCall.objects.filter(
        trace_id="trace-tool-usage"
    ).order_by("created_at", "id")
    action_entries = [step for step in steps if step.step_type == "llm_call"]
    assert action_entries[0].action == "call_tools"
    assert action_entries[0].llm_response_text
    llm_tool_calls = [step for step in steps if step.step_type == "mcp_tool_call"]
    assert [tool_call.tool_name for tool_call in llm_tool_calls] == [
        "inspect_proxy_activity",
        "inspect_live_fail2ban_activity",
    ]
    assert all(tool_call.status == "succeeded" for tool_call in llm_tool_calls)
    assert all(tool_call.arguments_hash for tool_call in llm_tool_calls)


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
    assert second_extra["tool_call_count"] == 0
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
