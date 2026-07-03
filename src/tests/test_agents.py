import json
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from llm_core.providers.mock import MockProvider
from llm_core.types import ResponseFormat, TextPart
from llm_core.usage import Usage
from pytest_mock import MockerFixture

from agents import MonitoringWorkflowAgent
from db.models import LogAnalysisLLMCall
from exceptions import (
    LogAnalysisAgentError,
    LogAnalysisHistoryComparisonServiceMissingException,
    McpClientError,
)
from mcp import McpWorkflowClient
from repositories import LLMCallRepository
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogAnalysisAllowedAction,
    LogAnalysisFingerprints,
    LogAnalysisHistoryComparisonStatus,
    LogAnalysisNextRequiredAction,
    LogAnalysisOut,
    LogAnalysisPromptPhase,
    LogAnalysisSeverity,
    LogCollectionWindow,
    LogSourceCollectionStatus,
    LogWorkspace,
    McpToolName,
    ProjectManifestSummary,
    RecommendedAction,
    WorkflowBootstrap,
    WorkflowSkill,
    WorkflowTool,
)
from tests.conftest import (
    PRIVATE_MONITORING_CONTEXT,
    AgentFactory,
    HistoryAgentFactory,
    build_collect_logs_artifact_payload,
)


def _fingerprints(payload: dict[str, object]) -> LogAnalysisFingerprints:
    return LogAnalysisFingerprints.model_validate(payload)


class FakeMcpWorkflowClient(McpWorkflowClient):
    def __init__(self) -> None:
        super().__init__(
            base_url="http://mcp.test/mcp",
            workflow_jwt="test-workflow-jwt",
        )
        self.calls: list[str] = []
        self.tool_results: dict[str, dict[str, object]] = {
            McpToolName.GROUP_ERRORS: {
                "action": McpToolName.GROUP_ERRORS,
                "project_name": "demo-shop",
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
            workflow_name=McpToolName.ANALYZE_DAILY_LOG_BUNDLE,
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
                    tool_name=McpToolName.GROUP_ERRORS,
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
                    tool_name=McpToolName.INSPECT_PROXY_ACTIVITY,
                    description="Inspect proxy status distribution.",
                    arguments=[],
                ),
                WorkflowTool(
                    tool_name="inspect_live_fail2ban_activity",
                    description="Inspect live fail2ban jails.",
                    arguments=[],
                ),
                WorkflowTool(
                    tool_name=McpToolName.GREP_LOG_SNAPSHOT,
                    description="Search collected log snapshots.",
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
                requested_project_names=["demo-shop", "shop"],
                next_step_tips=["Use group_snapshot_errors before final report."],
                warnings=["nginx stderr unavailable"],
                include_unavailable_nginx=True,
            )
        )

    async def list_projects(self) -> list[ProjectManifestSummary]:
        self.calls.append(McpToolName.LIST_PROJECTS)
        return [
            ProjectManifestSummary(
                project_name="demo-shop",
                project_summary="Demo shop project.",
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


def _final_report_payload(
    *,
    summary: str = "Logs are stable.",
    severity: str = LogAnalysisSeverity.INFO,
    evidence: list[str] | None = None,
) -> dict[str, object]:
    return {
        "action": LogAnalysisAllowedAction.FINAL_REPORT,
        "summary": summary,
        "severity": severity,
        "severity_rationale": f"{severity} based on deterministic context.",
        "key_findings": ["History comparison was reviewed."],
        "evidence": evidence or ["Grouped-error history comparison was reviewed."],
        "coverage_gaps": [],
        "recommendations": "Continue monitoring.",
        "watch_only_items": ["Known scanner noise."],
        "trend_summary": "Grouped-error fingerprints were compared with history.",
    }


def _group_errors_result(
    *,
    project_name: str,
    fingerprint: str,
    severity: str = "medium",
    count: int = 5,
    category: str = "http_4xx",
    status_code: int = 404,
    source_key: str = "nginx",
    request_path: str = "/.env",
    message_summary: str = "Grouped scanner probe",
) -> dict[str, object]:
    return {
        "action": McpToolName.GROUP_ERRORS,
        "project_name": project_name,
        "grouped_error_count": 1,
        "matching_line_count": count,
        "truncated": False,
        "groups": [
            {
                "fingerprint": fingerprint,
                "category": category,
                "severity": severity,
                "count": count,
                "source_keys": [source_key],
                "request_paths": [request_path],
                "status_codes": [status_code],
                "levels": [],
                "message_summary": message_summary,
                "first_timestamp": "2026-05-19T02:00:00Z",
                "last_timestamp": "2026-05-19T03:00:00Z",
            }
        ],
    }


def test_group_errors_arguments_skip_unavailable_snapshot_sources() -> None:
    current_logs = CollectLogsArtifact.model_validate(
        build_collect_logs_artifact_payload(
            resolved_source_keys=["backend", "nginx"],
            include_unavailable_nginx=True,
        )
    )

    arguments = MonitoringWorkflowAgent._build_group_errors_arguments_from_current_logs(
        current_logs
    )

    assert arguments == [{"project_name": "demo-shop", "source_keys": ["backend"]}]


class FakeMcpWorkflowClientWithoutProjects(FakeMcpWorkflowClient):
    async def list_projects(self) -> list[ProjectManifestSummary]:
        self.calls.append(McpToolName.LIST_PROJECTS)
        return []


def test_monitoring_workflow_agent_requires_history_service_when_enabled() -> None:
    with pytest.raises(
        LogAnalysisHistoryComparisonServiceMissingException,
        match="History comparison service is required",
    ):
        MonitoringWorkflowAgent(
            FakeMcpWorkflowClient(),
            llm_provider=MockProvider(),
            private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
            history_comparison_enabled=True,
        )


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_collects_logs_and_prepares_prompt_context(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    analysis_date = date(2026, 5, 19)
    log_window = LogCollectionWindow(
        since="2026-05-19T00:00:00Z",
        until="2026-05-20T00:00:00Z",
        since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
        until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
    )
    final_report_payload: dict[str, object] = {
        "action": LogAnalysisAllowedAction.FINAL_REPORT,
        "summary": "Logs are mostly healthy with one unavailable source.",
        "severity": LogAnalysisSeverity.WARNING,
        "severity_rationale": "WARNING because one source was unavailable.",
        "key_findings": ["nginx stderr was unavailable"],
        "evidence": ["group_errors found one unavailable source"],
        "coverage_gaps": ["nginx stderr source was unavailable"],
        "recommendations": "Check the nginx stderr source mapping.",
        "watch_only_items": ["Routine bot traffic without service impact"],
        "trend_summary": "No historical trend was available.",
    }
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {"project_name": "demo-shop"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(final_report_payload),
        usage=Usage(prompt_tokens=100, completion_tokens=40, total_tokens=140, cost_usd=0.01),
    )
    agent = agent_factory(mcp_client, llm_provider)

    context: LogAnalysisAgentContext = await agent.run_log_analysis(
        analysis_date=analysis_date,
        log_window=log_window,
        historical_context="",
    )

    assert mcp_client.calls == [
        "get_workflow_bundle",
        "read_resource:skill://workflow/severity_guide",
        McpToolName.LIST_PROJECTS,
        "collect_logs:2026-05-19T00:00:00Z:2026-05-20T00:00:00Z",
        (
            "call_deterministic_tool:group_errors:{'project_name': 'demo-shop', "
            "'source_keys': ['backend']}"
        ),
        "call_deterministic_tool:group_errors:{'project_name': 'demo-shop'}",
    ]
    assert context.workflow.workflow_name == McpToolName.ANALYZE_DAILY_LOG_BUNDLE
    collected_project = context.collect_logs.projects[0]
    prompt_project = context.prompt.context.collection.projects[0]
    assert collected_project.resolved_source_keys == prompt_project.resolved_source_keys
    assert PRIVATE_MONITORING_CONTEXT in context.prompt.system_prompt
    assert PRIVATE_MONITORING_CONTEXT not in context.prompt.user_prompt
    assert context.prompt.context.analysis_date == analysis_date
    assert [project.project_name for project in context.prompt.context.available_projects] == (
        context.collect_logs.requested_project_names
    )
    assert prompt_project.snapshot_dir == collected_project.snapshot_dir
    assert prompt_project.sources[0].source_key == collected_project.sources[0].source_key
    assert context.prompt.context.available_tools[0].tool_name == McpToolName.GROUP_ERRORS
    assert len(context.tool_results) == 1
    assert context.tool_results[0].tool_name == McpToolName.GROUP_ERRORS
    assert context.tool_results[0].structured_content["action"] == McpToolName.GROUP_ERRORS
    assert context.final_report.summary == final_report_payload["summary"]
    assert context.final_report.severity == final_report_payload["severity"]
    assert context.final_report.severity_rationale == final_report_payload["severity_rationale"]
    assert context.final_report.evidence == final_report_payload["evidence"]
    assert context.final_report.coverage_gaps == final_report_payload["coverage_gaps"]
    assert context.final_report.watch_only_items == final_report_payload["watch_only_items"]
    assert context.llm_tokens_used == 140
    assert context.llm_cost_usd == 0.01
    assert len(llm_provider.requests) == 2
    llm_request = llm_provider.requests[0]
    assert llm_request.options.response_format is ResponseFormat.JSON_OBJECT
    assert llm_request.metadata["workflow_name"] == McpToolName.ANALYZE_DAILY_LOG_BUNDLE
    assert llm_request.messages[0].role == "system"
    assert llm_request.messages[1].role == "user"
    followup_request = llm_provider.requests[1]
    assert followup_request.messages[-1].role == "user"
    assert [message.role for message in followup_request.messages] == ["system", "user", "user"]
    followup_text: str = cast(TextPart, followup_request.messages[-1].parts[0]).text
    followup_payload = json.loads(followup_text)
    assert followup_payload["previous_action"]["action"] == LogAnalysisAllowedAction.CALL_TOOLS
    assert followup_payload["tool_results"][0]["tool_name"] == McpToolName.GROUP_ERRORS
    tool_status_by_name = {
        status["tool_name"]: status for status in followup_payload["available_tool_status"]
    }
    assert tool_status_by_name[McpToolName.GROUP_ERRORS]["already_called"] is True
    assert tool_status_by_name[McpToolName.INSPECT_PROXY_ACTIVITY]["already_called"] is False
    optional_skill_status_by_name = {
        status["skill_name"]: status for status in followup_payload["optional_skill_status"]
    }
    assert optional_skill_status_by_name["bot_detection"]["already_retrieved"] is False
    assert followup_payload["initial_context_reference"]["current_coverage_available"] is True
    assert followup_payload["current_tool_result_count"] == 1
    followup_instructions = "\n".join(followup_payload["instructions"])
    assert "blocked_probe" in followup_instructions
    assert "empty upstream_addr/upstream_status" in followup_instructions
    assert "real upstream 5xx" in followup_instructions
    assert followup_payload["next_required_action"] == (
        LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    )
    assert "blocked scanner/probe noise" not in context.prompt.user_prompt
    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["analysis_date"] == analysis_date.isoformat()
    assert user_prompt["current_phase"] == LogAnalysisPromptPhase.INSPECT_COLLECTED_LOGS
    assert user_prompt["evidence"]["kind"] == "grouped_error_baseline"
    assert user_prompt["evidence"]["previous_grouped_errors"] is None
    assert user_prompt["evidence"]["current_grouped_errors"]["available"] is True
    assert user_prompt["final_report_allowed"] is True
    assert user_prompt["allowed_actions"] == [
        LogAnalysisAllowedAction.CALL_TOOLS,
        LogAnalysisAllowedAction.READ_SKILLS,
        LogAnalysisAllowedAction.FINAL_REPORT,
    ]
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert user_prompt["completed_steps"] == [
        McpToolName.ANALYZE_DAILY_LOG_BUNDLE,
        "read_mandatory_skills",
        McpToolName.LIST_PROJECTS,
        McpToolName.COLLECT_LOGS,
    ]
    assert user_prompt["available_projects"][0]["project_name"] == (
        context.prompt.context.available_projects[0].project_name
    )
    assert user_prompt["mandatory_skills"][0]["name"] == context.workflow.mandatory_skills[0].name
    assert user_prompt["mandatory_skills"][0]["resource_uri"] == (
        context.workflow.mandatory_skills[0].resource_uri
    )
    assert user_prompt["optional_skills"][0]["when_useful"] == (
        context.workflow.optional_skills[0].when_useful
    )
    assert user_prompt["collection"]["projects"][0]["snapshot_dir"] == prompt_project.snapshot_dir
    assert user_prompt["snapshot_access"]["workspace"] == LogWorkspace.WORKFLOW
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
    assert "blocked_probe" in joined_instructions
    assert "empty upstream_addr/upstream_status" in joined_instructions
    assert "real upstream 5xx" in joined_instructions
    assert set(user_prompt["report_contract"]) == {
        "summary",
        "severity",
        "severity_rationale",
        "key_findings",
        "evidence",
        "grouped-error baseline review",
        "grouped-error baseline resolved sensitive access",
        "resolved high-severity history",
        "coverage_gaps",
        "recommendations",
        "watch_only_items",
        "trend_summary",
    }
    assert all(user_prompt["report_contract"].values())
    assert "analysis_date:" not in context.prompt.user_prompt


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_includes_historical_context_in_system_prompt(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {"project_name": "demo-shop"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Logs are healthy.",
                "severity": LogAnalysisSeverity.INFO,
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
    agent = agent_factory(mcp_client, llm_provider)

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
    assert '"historical_context_available":true' in followup_text
    assert "do not claim no historical data was provided" in followup_text


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_includes_previous_analysis_in_user_prompt(
    history_agent_factory: HistoryAgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {"project_name": "demo-shop"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Logs match previous scanner noise.",
                "severity": LogAnalysisSeverity.INFO,
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
        severity=LogAnalysisSeverity.INFO,
        trend_summary="Scanner noise was stable.",
        fingerprints=_fingerprints(
            {
                "report": {"severity": LogAnalysisSeverity.INFO},
                "grouped_error_runs": [
                    {
                        "arguments": {
                            "project_name": "demo-shop",
                            "source_keys": ["backend", "nginx"],
                        },
                        "result": {
                            "project_name": "demo-shop",
                            "grouped_error_count": 1,
                            "groups": [
                                {
                                    "fingerprint": "nginx:http_4xx:404:/.env",
                                    "project_name": "demo-shop",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 4,
                                    "source_keys": ["nginx"],
                                    "request_paths": ["/.env"],
                                    "status_codes": [404],
                                    "message_summary": "Scanner probe",
                                }
                            ],
                        },
                    }
                ],
            }
        ),
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
                    "project_name": "demo-shop",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": LogSourceCollectionStatus.COLLECTED,
                            "line_count": 120,
                            "zero_lines": False,
                        },
                        {
                            "source_key": "nginx",
                            "status": LogSourceCollectionStatus.UNAVAILABLE,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ],
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = history_agent_factory(mcp_client, llm_provider)

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
    assert user_prompt["evidence"]["history_comparison"]["status"] == "available"
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert user_prompt["final_report_allowed"] is True
    assert user_prompt["evidence_mode"] == "current_grouped_errors_available"
    assert user_prompt["current_tool_result_count"] == 0
    assert user_prompt["evidence"]["prompt_compacted"].get("previous_grouped_errors") is None
    assert user_prompt["evidence"]["prompt_compacted"].get("current_grouped_errors") is None
    grouped_error_diff = user_prompt["evidence"]["prompt_compacted"]["grouped_error_diff"]
    assert grouped_error_diff["available"] is True
    assert grouped_error_diff["current_tool_scope_by_project"] == {"demo-shop": ["backend"]}
    assert grouped_error_diff["previous_group_count"] == 1
    assert grouped_error_diff["current_group_count"] == 0
    assert grouped_error_diff["resolved_fingerprint_count"] == 1
    assert grouped_error_diff["new_high_severity_fingerprint_count"] == 0
    assert grouped_error_diff["resolved_high_severity_current_scope_covered"] is True
    assert grouped_error_diff["current_changed_examples"] == []
    assert len(grouped_error_diff["previous_changed_examples"]) == 1
    assert grouped_error_diff["previous_changed_examples"][0]["fingerprint"] == (
        previous_analysis.fingerprints.grouped_error_runs[0].result.groups[0].fingerprint
    )
    assert user_prompt["current_coverage"] == {
        "zero_line_sources": [],
        "unavailable_sources": ["demo-shop.nginx"],
    }
    assert user_prompt["evidence"]["prompt_compacted"]["source_coverage"] == {
        "available": True,
        "source_coverage_changed": False,
        "changed_sources": [],
        "tool_scope_by_project": {},
        "recommended_action": RecommendedAction.LLM_MAY_DECIDE,
        "rationale": (
            "Previous and current source coverage state metadata match. Let the LLM "
            "decide whether current deterministic tools are needed before final_report."
        ),
    }
    previous_prompt = user_prompt["previous_analysis"]
    assert previous_prompt["analysis_date"] == "2026-05-18"
    assert previous_prompt["summary"] == "Known scanner noise only."
    assert previous_prompt["severity"] == LogAnalysisSeverity.INFO
    assert previous_prompt["trend_summary"] == "Scanner noise was stable."
    assert previous_prompt["fingerprints"]["report"]["severity"] == LogAnalysisSeverity.INFO
    assert previous_prompt["fingerprints"]["grouped_error_runs"] == []
    assert "grouped_error_signals" not in previous_prompt["fingerprints"]
    assert previous_prompt["evidence_fingerprints"] == ["evidence:abc"]
    assert previous_prompt["known_patterns"] == [{"pattern": "Routine bot traffic."}]
    assert previous_prompt["coverage_snapshot"] == {
        "totals": {
            "project_count": 1,
            "source_count": 2,
            "zero_line_sources": 1,
        }
    }
    assert previous_prompt["fingerprint_version"] == "log-analysis-fingerprint-v1"
    assert "projects" not in user_prompt["previous_analysis"]["coverage_snapshot"]
    followup_prompt = json.loads(
        cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    )
    assert "previous_analysis" not in followup_prompt
    assert "history_comparison" not in followup_prompt
    assert "current_coverage" not in followup_prompt
    assert followup_prompt["initial_context_reference"]["previous_analysis_available"] is True
    assert followup_prompt["initial_context_reference"]["history_comparison_status"] == "available"
    assert followup_prompt["evidence_mode"] == "current_tool_results_available"
    assert followup_prompt["current_tool_result_count"] == 1
    assert followup_prompt["next_required_action"] == (
        LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    )
    assert followup_prompt["final_report_allowed"] is True


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_preloads_group_errors_when_comparison_disabled(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Previous history alone is enough.",
                "severity": LogAnalysisSeverity.INFO,
                "severity_rationale": "This should be rejected until current tools run.",
                "key_findings": ["Previous history was stable."],
                "evidence": ["previous_analysis"],
                "coverage_gaps": [],
                "recommendations": "Continue monitoring.",
                "watch_only_items": [],
                "trend_summary": "Previous run was stable.",
            }
        )
    )
    previous_analysis = LogAnalysisOut(
        id=9,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Previous scanner noise only.",
        severity=LogAnalysisSeverity.INFO,
        trend_summary="Previous run was stable.",
        fingerprints=_fingerprints(
            {
                "report": {"severity": LogAnalysisSeverity.INFO},
                "grouped_error_runs": [
                    {
                        "arguments": {
                            "project_name": "demo-shop",
                            "source_keys": ["backend", "nginx"],
                        },
                        "result": {
                            "project_name": "demo-shop",
                            "grouped_error_count": 1,
                            "groups": [
                                {
                                    "fingerprint": "nginx:http_4xx:404:/.env",
                                    "project_name": "demo-shop",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 4,
                                    "source_keys": ["nginx"],
                                    "request_paths": ["/.env"],
                                    "status_codes": [404],
                                    "message_summary": "Scanner probe",
                                }
                            ],
                        },
                    }
                ],
            }
        ),
        evidence_fingerprints=["evidence:previous"],
        known_patterns=[{"pattern": "Scanner noise."}],
        coverage_snapshot={"totals": {"project_count": 1}},
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = agent_factory(mcp_client, llm_provider)

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
    assert user_prompt["evidence"]["kind"] == "grouped_error_baseline"
    assert "grouped_error_diff" not in user_prompt["evidence"]
    previous_grouped_errors = user_prompt["evidence"]["previous_grouped_errors"]
    assert previous_grouped_errors["available"] is True
    assert previous_grouped_errors["label"] == "previous"
    assert previous_grouped_errors["tool_scope_by_project"] == {
        "demo-shop": ["backend", "nginx"],
    }
    assert previous_grouped_errors["run_count"] == 1
    assert previous_grouped_errors["group_count"] == 1
    assert previous_grouped_errors["severity_counts"] == {"medium": 1}
    assert previous_grouped_errors["category_counts"] == {"http_4xx": 1}
    assert previous_grouped_errors["fingerprints"] == [
        {
            "fingerprint": "nginx:http_4xx:404:/.env",
            "project_name": "demo-shop",
            "category": "http_4xx",
            "severity": "medium",
            "source_keys": ["nginx"],
            "status_codes": [404],
        }
    ]
    assert user_prompt["evidence"]["current_grouped_errors"]["available"] is True
    assert user_prompt["evidence"]["current_grouped_errors"]["label"] == "current"
    assert user_prompt["evidence"]["current_grouped_errors"]["run_count"] == 1
    assert user_prompt["evidence"]["current_grouped_errors"]["tool_scope_by_project"] == {
        "demo-shop": ["backend"],
    }
    decision_prompt = user_prompt["evidence"]["decision_prompt"]
    assert decision_prompt["mode"] == "no_compare_history"
    assert any(
        "Do not decide from group_count or run_count alone" in rule
        for rule in decision_prompt["decision_rules"]
    )
    assert any(
        "semantic fingerprint families" in rule for rule in decision_prompt["decision_rules"]
    )
    assert any(
        "cost-saving final_report path is only for a stable baseline" in rule
        for rule in decision_prompt["decision_rules"]
    )
    assert any(
        "visible current fingerprints introduce, remove, or shift source ownership" in rule
        for rule in decision_prompt["decision_rules"]
    )
    assert user_prompt["previous_analysis"]["summary"] == "Previous scanner noise only."
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert user_prompt["final_report_allowed"] is True
    assert user_prompt["evidence_mode"] == "current_grouped_errors_available"
    assert user_prompt["current_tool_result_count"] == 0
    assert context.tool_results == []
    assert (
        user_prompt["previous_analysis"]["fingerprints"]["grouped_error_history_summary"]["detail"]
        == "Full grouped-error history is included as previous fingerprint baseline."
    )
    assert "call_deterministic_tool:group_errors" in "\n".join(mcp_client.calls)
    assert len(llm_provider.requests) == 1


@pytest.mark.asyncio
async def test_no_compare_grouped_error_prompt_compacts_broad_baseline_examples(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(json.dumps(_final_report_payload()))

    previous_groups = [
        {
            "fingerprint": f"nginx:http_4xx:404:/old-{index}.php",
            "project_name": "demo-shop",
            "category": "http_4xx",
            "severity": "medium",
            "count": 1,
            "source_keys": ["nginx"],
            "request_paths": [f"/old-{index}.php"],
            "status_codes": [404],
            "message_summary": "Old scanner probe",
        }
        for index in range(25)
    ] + [
        {
            "fingerprint": f"backend:http_4xx:404:/old-backend-{index}.json",
            "project_name": "demo-shop",
            "category": "http_4xx",
            "severity": "medium",
            "count": 2,
            "source_keys": ["backend"],
            "request_paths": [f"/old-backend-{index}.json"],
            "status_codes": [404],
            "message_summary": "Old backend probe",
        }
        for index in range(5)
    ]
    current_groups = [
        {
            "fingerprint": f"backend:http_4xx:404:/new-{index}.json",
            "category": "http_4xx",
            "severity": "medium",
            "count": 1,
            "source_keys": ["backend"],
            "request_paths": [f"/new-{index}.json"],
            "status_codes": [404],
            "message_summary": "New backend probe",
        }
        for index in range(25)
    ] + [
        {
            "fingerprint": f"traefik:http_4xx:404:/new-traefik-{index}.json",
            "category": "http_4xx",
            "severity": "medium",
            "count": 2,
            "source_keys": ["traefik"],
            "request_paths": [f"/new-traefik-{index}.json"],
            "status_codes": [404],
            "message_summary": "New traefik probe",
        }
        for index in range(5)
    ]
    mcp_client.tool_results[McpToolName.GROUP_ERRORS] = {
        "action": McpToolName.GROUP_ERRORS,
        "project_name": "demo-shop",
        "grouped_error_count": len(current_groups),
        "groups": current_groups,
    }
    previous_analysis = LogAnalysisOut(
        id=91,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Previous grouped errors were old nginx probes.",
        severity=LogAnalysisSeverity.INFO,
        fingerprints=_fingerprints(
            {
                "report": {"severity": LogAnalysisSeverity.INFO},
                "grouped_error_runs": [
                    {
                        "arguments": {
                            "project_name": "demo-shop",
                            "source_keys": ["backend", "nginx"],
                        },
                        "result": {
                            "project_name": "demo-shop",
                            "grouped_error_count": len(previous_groups),
                            "groups": previous_groups,
                        },
                    }
                ],
            }
        ),
        evidence_fingerprints=[],
        known_patterns=[],
        coverage_snapshot={"projects": []},
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = agent_factory(mcp_client, llm_provider)

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
    previous_evidence = user_prompt["evidence"]["previous_grouped_errors"]
    current_evidence = user_prompt["evidence"]["current_grouped_errors"]
    assert len(previous_evidence["fingerprints"]) == 30
    assert {tuple(row["source_keys"]) for row in previous_evidence["fingerprints"]} == {
        ("backend",),
        ("nginx",),
    }
    assert "omitted_example_count" not in previous_evidence
    assert all("request_paths" not in row for row in previous_evidence["fingerprints"])
    assert all("message_summary" not in row for row in previous_evidence["fingerprints"])
    assert len(current_evidence["fingerprints"]) == 30
    assert {tuple(row["source_keys"]) for row in current_evidence["fingerprints"]} == {
        ("backend",),
        ("traefik",),
    }
    assert "omitted_example_count" not in current_evidence


@pytest.mark.asyncio
async def test_agent_uses_current_grouped_baseline_without_previous_run(
    history_agent_factory: HistoryAgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {"project_name": "demo-shop"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(json.dumps(_final_report_payload()))
    agent = history_agent_factory(mcp_client, llm_provider)

    context = await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["evidence"]["kind"] == "grouped_error_baseline"
    assert user_prompt["previous_analysis"] is None
    assert user_prompt["evidence"]["previous_grouped_errors"] is None
    assert user_prompt["evidence"]["current_grouped_errors"]["available"] is True
    assert "history_comparison" not in user_prompt["evidence"]
    assert "prompt_compacted" not in user_prompt["evidence"]
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_compares_current_grouped_errors_with_history(
    history_agent_factory: HistoryAgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results[McpToolName.GROUP_ERRORS] = _group_errors_result(
        project_name="demo-shop",
        fingerprint="frontend:http_4xx:404:/favicon.png",
        severity="medium",
        count=6,
        source_key="frontend",
        request_path="/favicon.png",
        message_summary="Grouped frontend favicon probe",
    )
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            _final_report_payload(
                summary="Grouped-error fingerprints match the previous run.",
                evidence=["Grouped-error history comparison showed no new fingerprints."],
            )
        )
    )
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Known scanner noise only.",
        severity=LogAnalysisSeverity.INFO,
        trend_summary="Scanner noise was stable.",
        fingerprints=_fingerprints(
            {
                "grouped_error_runs": [
                    {
                        "arguments": {
                            "project_name": "demo-shop",
                            "source_keys": ["backend", "frontend"],
                        },
                        "result": {
                            "groups": [
                                {
                                    "fingerprint": "frontend:http_4xx:404:/favicon.png",
                                    "project_name": "demo-shop",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 5,
                                    "source_keys": ["frontend"],
                                    "request_paths": ["/favicon.png"],
                                    "status_codes": [404],
                                    "levels": [],
                                    "message_summary": "Grouped frontend favicon probe",
                                    "first_timestamp": "2026-05-18T02:00:00Z",
                                    "last_timestamp": "2026-05-18T03:00:00Z",
                                }
                            ],
                        },
                    }
                ],
            }
        ),
        evidence_fingerprints=["tool:group_errors:abc"],
        known_patterns=[{"pattern": "Known scanner noise."}],
        coverage_snapshot={
            "projects": [
                {
                    "project_name": "demo-shop",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": LogSourceCollectionStatus.COLLECTED,
                            "line_count": 120,
                            "zero_lines": False,
                        },
                        {
                            "source_key": "nginx",
                            "status": LogSourceCollectionStatus.UNAVAILABLE,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ]
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = history_agent_factory(mcp_client, llm_provider)

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

    assert mcp_client.calls == [
        "get_workflow_bundle",
        "read_resource:skill://workflow/severity_guide",
        McpToolName.LIST_PROJECTS,
        "collect_logs:2026-05-19T00:00:00Z:2026-05-20T00:00:00Z",
        (
            "call_deterministic_tool:group_errors:{'project_name': 'demo-shop', "
            "'source_keys': ['backend']}"
        ),
    ]
    user_prompt = json.loads(context.prompt.user_prompt)
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert user_prompt["current_tool_result_count"] == 0
    assert user_prompt["evidence_mode"] == "current_grouped_errors_available"
    grouped_error_diff = user_prompt["evidence"]["prompt_compacted"]["grouped_error_diff"]
    assert grouped_error_diff["available"] is True
    assert grouped_error_diff["current_tool_scope_by_project"] == {"demo-shop": ["backend"]}
    assert grouped_error_diff["previous_group_count"] == 1
    assert grouped_error_diff["current_group_count"] == 1
    assert grouped_error_diff["persisting_fingerprint_count"] == 1
    assert grouped_error_diff["worsened_fingerprint_count"] == 1
    assert grouped_error_diff["evidence_quality_warnings"] == [
        "worsened_grouped_error_fingerprints_present"
    ]
    assert len(grouped_error_diff["current_changed_examples"]) == 1
    assert len(grouped_error_diff["previous_changed_examples"]) == 1
    assert grouped_error_diff["current_changed_examples"][0]["fingerprint"] == (
        grouped_error_diff["previous_changed_examples"][0]["fingerprint"]
    )
    assert context.tool_results == []
    assert len(llm_provider.requests) == 1


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_surfaces_new_high_severity_grouped_errors(
    history_agent_factory: HistoryAgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results[McpToolName.GROUP_ERRORS] = _group_errors_result(
        project_name="demo-shop",
        fingerprint="nginx:http_5xx:500:/api",
        severity="high",
        count=3,
        category="http_5xx",
        status_code=500,
    )
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.INSPECT_PROXY_ACTIVITY,
                        "arguments": {"project_name": "demo-shop"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(_final_report_payload(severity=LogAnalysisSeverity.WARNING))
    )
    mcp_client.tool_results[McpToolName.INSPECT_PROXY_ACTIVITY] = {
        "action": McpToolName.INSPECT_PROXY_ACTIVITY,
        "project_name": "demo-shop",
        "status_class_counts": {"5xx": 3},
        "upstream_error_count": 0,
    }
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Known scanner noise only.",
        severity=LogAnalysisSeverity.INFO,
        fingerprints=_fingerprints(
            {
                "grouped_error_runs": [
                    {
                        "result": {
                            "groups": [
                                {
                                    "fingerprint": "nginx:http_4xx:404:/.env",
                                    "project_name": "demo-shop",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 5,
                                    "source_keys": ["nginx"],
                                    "request_paths": ["/.env"],
                                    "status_codes": [404],
                                    "levels": [],
                                    "message_summary": "Grouped scanner probe",
                                    "first_timestamp": "2026-05-18T02:00:00Z",
                                    "last_timestamp": "2026-05-18T03:00:00Z",
                                }
                            ],
                        }
                    }
                ]
            }
        ),
        evidence_fingerprints=[],
        known_patterns=[],
        coverage_snapshot={
            "projects": [
                {
                    "project_name": "demo-shop",
                    "sources": [
                        {
                            "source_key": "nginx",
                            "status": LogSourceCollectionStatus.UNAVAILABLE,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ]
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = history_agent_factory(mcp_client, llm_provider)

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
    grouped_errors = user_prompt["evidence"]["prompt_compacted"]["grouped_error_diff"]
    assert grouped_errors["new_fingerprint_count"] == 1
    assert grouped_errors["resolved_fingerprint_count"] == 1
    assert grouped_errors["new_high_severity_fingerprints"] == ["nginx:http_5xx:500:/api"]
    assert grouped_errors["new_high_severity_fingerprint_count"] == 1
    assert grouped_errors["priority_current_examples"][0]["fingerprint"] == (
        "nginx:http_5xx:500:/api"
    )
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert [result.tool_name for result in context.tool_results] == [
        McpToolName.INSPECT_PROXY_ACTIVITY,
    ]
    followup_prompt = json.loads(
        cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    )
    initial_context_reference = followup_prompt["initial_context_reference"]
    assert initial_context_reference["historical_context_available"] is False
    assert initial_context_reference["previous_analysis_available"] is True
    assert (
        initial_context_reference["history_comparison_status"]
        == LogAnalysisHistoryComparisonStatus.AVAILABLE
    )
    assert initial_context_reference["history_comparison_has_grouped_error_diff"] is True
    assert initial_context_reference["current_coverage_available"] is True


@pytest.mark.asyncio
async def test_agent_relaxes_missing_log_guard_with_grouped_errors(
    history_agent_factory: HistoryAgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results[McpToolName.GROUP_ERRORS] = _group_errors_result(
        project_name="demo-shop",
        fingerprint="backend:http_4xx:404:/robots.txt",
        severity="medium",
        count=1,
        source_key="backend",
        request_path="/robots.txt",
        message_summary="Grouped backend robots probe",
    )
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            _final_report_payload(
                summary="Grouped errors were stable enough for a short report.",
                evidence=["Grouped-error comparison was current deterministic evidence."],
            )
        )
    )
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Known scanner noise only.",
        severity=LogAnalysisSeverity.INFO,
        fingerprints=_fingerprints(
            {
                "grouped_error_runs": [
                    {
                        "arguments": {
                            "project_name": "demo-shop",
                            "source_keys": ["backend"],
                        },
                        "result": {
                            "groups": [
                                {
                                    "fingerprint": "backend:http_4xx:404:/robots.txt",
                                    "project_name": "demo-shop",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 1,
                                    "source_keys": ["backend"],
                                    "request_paths": ["/robots.txt"],
                                    "status_codes": [404],
                                    "levels": [],
                                    "message_summary": "Grouped backend robots probe",
                                    "first_timestamp": "2026-05-18T02:00:00Z",
                                    "last_timestamp": "2026-05-18T03:00:00Z",
                                }
                            ],
                        },
                    }
                ],
            }
        ),
        evidence_fingerprints=[],
        known_patterns=[],
        coverage_snapshot={
            "projects": [
                {
                    "project_name": "demo-shop",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": LogSourceCollectionStatus.COLLECTED,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                        {
                            "source_key": "nginx",
                            "status": LogSourceCollectionStatus.UNAVAILABLE,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ]
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = history_agent_factory(mcp_client, llm_provider)

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
    assert (
        user_prompt["evidence"]["prompt_compacted"]["source_coverage"]["source_coverage_changed"]
        is True
    )
    assert (
        user_prompt["evidence"]["prompt_compacted"]["source_coverage"]["recommended_action"]
        == RecommendedAction.LLM_MAY_DECIDE
    )
    assert (
        user_prompt["evidence"]["prompt_compacted"]["source_coverage"]["tool_scope_by_project"]
        == {}
    )
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert user_prompt["final_report_allowed"] is True
    assert context.tool_results == []
    assert len(llm_provider.requests) == 1


@pytest.mark.asyncio
async def test_agent_rejects_broad_final_report_outside_grouped_error_scope(
    history_agent_factory: HistoryAgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results[McpToolName.GROUP_ERRORS] = _group_errors_result(
        project_name="demo-shop",
        fingerprint="frontend:http_4xx:404:/favicon.png",
        severity="medium",
        count=5,
        source_key="frontend",
        request_path="/favicon.png",
        message_summary="Grouped frontend favicon probe",
    )
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": (
                    "The demo-shop and host-security projects show stable operation with "
                    "no new or worsening 5xx or upstream errors."
                ),
                "severity": LogAnalysisSeverity.INFO,
                "severity_rationale": "No 5xx or upstream errors were detected.",
                "key_findings": [
                    "No 5xx or upstream errors were detected in any collected logs for "
                    "demo-shop or host-security projects."
                ],
                "evidence": ["Current grouped_errors covered demo-shop frontend only."],
                "coverage_gaps": [],
                "recommendations": "Continue monitoring.",
                "watch_only_items": [],
                "trend_summary": "Stable operation persisted.",
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            _final_report_payload(
                summary=(
                    "Demo shop frontend grouped-error fingerprints match previous "
                    "history; other projects were not reanalyzed by current tools."
                ),
                evidence=[
                    "Current grouped_errors covered demo-shop frontend.",
                    "Previous analysis is comparison context only.",
                ],
            )
        )
    )
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Known scanner noise only.",
        severity=LogAnalysisSeverity.INFO,
        trend_summary="Scanner noise was stable.",
        fingerprints=_fingerprints(
            {
                "grouped_error_runs": [
                    {
                        "arguments": {
                            "project_name": "demo-shop",
                            "source_keys": ["frontend"],
                        },
                        "result": {
                            "groups": [
                                {
                                    "fingerprint": "frontend:http_4xx:404:/favicon.png",
                                    "project_name": "demo-shop",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 5,
                                    "source_keys": ["frontend"],
                                    "request_paths": ["/favicon.png"],
                                    "status_codes": [404],
                                    "levels": [],
                                    "message_summary": "Grouped frontend favicon probe",
                                }
                            ],
                        },
                    }
                ],
            }
        ),
        evidence_fingerprints=["tool:group_errors:abc"],
        known_patterns=[{"pattern": "Known scanner noise."}],
        coverage_snapshot={
            "projects": [
                {
                    "project_name": "demo-shop",
                    "sources": [
                        {
                            "source_key": "frontend",
                            "status": LogSourceCollectionStatus.COLLECTED,
                            "line_count": 120,
                            "zero_lines": False,
                        }
                    ],
                }
            ]
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = history_agent_factory(mcp_client, llm_provider)

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

    assert context.final_report.summary == (
        "Demo shop frontend grouped-error fingerprints match previous history; "
        "other projects were not reanalyzed by current tools."
    )
    assert len(llm_provider.requests) == 2
    correction_prompt = cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    correction_payload = json.loads(correction_prompt)
    assert "unsupported_history_comparison_claims" in correction_prompt
    assert "current_grouped_error_scope_by_project" in correction_prompt
    assert correction_payload["rejected_claim_repair_rules"] == [
        "Do not claim overall service health or stable operation.",
        "Do not claim no service impact.",
        (
            "Do not claim no upstream failures, no 5xx errors, or no issues outside "
            "the grouped-error evidence scope."
        ),
        (
            "Scope every current-run health statement to current_grouped_error_scope_by_project "
            "or to inspected grouped-error evidence."
        ),
        (
            "Use inspected tool results as evidence only when the specific tool result "
            "supports the specific claim."
        ),
    ]
    assert correction_payload["allowed_replacement_claim_examples"] == [
        (
            "No supported evidence of service-impacting grouped-error changes was present "
            "inside the inspected grouped-error scope."
        ),
        (
            "Current grouped-error comparison covered only the listed project/source keys; "
            "other sources were not reanalysed by grouped-error evidence."
        ),
    ]
    assert correction_payload["forbidden_claim_examples"] == [
        "real routes served normally",
        "no service impact",
        "no upstream errors",
        "no 5xx errors",
        "stable operation",
        "TLS is healthy",
    ]
    assert "host-security" in correction_prompt


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_reduces_iterations_for_stable_history(
    agent_factory: AgentFactory,
    history_agent_factory: HistoryAgentFactory,
) -> None:
    full_mcp_client = FakeMcpWorkflowClient()
    full_llm_provider = MockProvider()
    full_llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {"project_name": "demo-shop"},
                    }
                ],
            }
        ),
        usage=Usage(prompt_tokens=120, completion_tokens=30, total_tokens=150, cost_usd=0.01),
    )
    full_llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.READ_SKILLS,
                "skill_names": ["bot_detection"],
            }
        ),
        usage=Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120, cost_usd=0.008),
    )
    full_llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Full analysis used tools and optional skill guidance.",
                "severity": LogAnalysisSeverity.INFO,
                "severity_rationale": "INFO because tool results found no service impact.",
                "key_findings": ["Tool loop completed."],
                "evidence": ["group_errors and bot_detection were reviewed."],
                "coverage_gaps": [],
                "recommendations": "Continue monitoring.",
                "watch_only_items": ["Routine bot traffic."],
                "trend_summary": "No previous structured baseline was available.",
            }
        ),
        usage=Usage(prompt_tokens=140, completion_tokens=50, total_tokens=190, cost_usd=0.012),
    )
    full_agent = agent_factory(full_mcp_client, full_llm_provider)

    full_context: LogAnalysisAgentContext = await full_agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    stable_mcp_client = FakeMcpWorkflowClient()
    stable_mcp_client.tool_results[McpToolName.GROUP_ERRORS] = _group_errors_result(
        project_name="demo-shop",
        fingerprint="nginx:http_4xx:404:/.env",
        severity="medium",
        count=5,
    )
    stable_llm_provider = MockProvider()
    stable_llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Stable grouped-error history allowed a short delta report.",
                "severity": LogAnalysisSeverity.INFO,
                "severity_rationale": (
                    "INFO because current grouped-error fingerprints match the prior clean run."
                ),
                "key_findings": ["No grouped-error fingerprint delta was detected."],
                "evidence": ["Current grouped-error fingerprints matched previous history."],
                "coverage_gaps": [],
                "recommendations": "Continue monitoring.",
                "watch_only_items": ["Routine bot traffic."],
                "trend_summary": "No material change from the previous run.",
            }
        ),
        usage=Usage(prompt_tokens=90, completion_tokens=35, total_tokens=125, cost_usd=0.006),
    )
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Known scanner noise only.",
        severity=LogAnalysisSeverity.INFO,
        trend_summary="Scanner noise was stable.",
        fingerprints=_fingerprints(
            {
                "report": {"severity": LogAnalysisSeverity.INFO},
                "grouped_error_runs": [
                    {
                        "result": {
                            "groups": [
                                {
                                    "fingerprint": "nginx:http_4xx:404:/.env",
                                    "project_name": "demo-shop",
                                    "category": "http_4xx",
                                    "severity": "medium",
                                    "count": 5,
                                    "source_keys": ["nginx"],
                                    "request_paths": ["/.env"],
                                    "status_codes": [404],
                                    "levels": [],
                                    "message_summary": "Grouped scanner probe",
                                    "first_timestamp": "2026-05-18T02:00:00Z",
                                    "last_timestamp": "2026-05-18T03:00:00Z",
                                }
                            ],
                        }
                    }
                ],
            }
        ),
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
                    "project_name": "demo-shop",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": LogSourceCollectionStatus.COLLECTED,
                            "line_count": 120,
                            "zero_lines": False,
                        },
                        {
                            "source_key": "nginx",
                            "status": LogSourceCollectionStatus.UNAVAILABLE,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ],
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    stable_agent = history_agent_factory(stable_mcp_client, stable_llm_provider)

    stable_context: LogAnalysisAgentContext = await stable_agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
        previous_analysis=previous_analysis,
    )

    stable_prompt = json.loads(stable_context.prompt.user_prompt)
    assert stable_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert stable_prompt["evidence_mode"] == "current_grouped_errors_available"
    assert stable_prompt["current_tool_result_count"] == 0
    assert (
        stable_prompt["evidence"]["prompt_compacted"]["grouped_error_diff"]["new_fingerprint_count"]
        == 0
    )
    assert stable_context.tool_results == []
    assert len(stable_llm_provider.requests) == 1
    assert stable_context.llm_tokens_used < full_context.llm_tokens_used
    assert stable_context.llm_cost_usd < full_context.llm_cost_usd
    assert len(stable_llm_provider.requests) < len(full_llm_provider.requests)


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_flags_changed_history_coverage(
    history_agent_factory: HistoryAgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Source coverage changed.",
                "severity": LogAnalysisSeverity.INFO,
                "severity_rationale": "Source coverage changed but no report was escalated.",
                "key_findings": ["Source coverage changed."],
                "evidence": [
                    "history_comparison.source_coverage showed changed source coverage state."
                ],
                "coverage_gaps": [],
                "recommendations": "Inspect changed source coverage state.",
                "watch_only_items": [],
                "trend_summary": "Source coverage changed.",
            }
        )
    )
    previous_analysis = LogAnalysisOut(
        id=8,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        analysis_date=date(2026, 5, 18),
        status="succeeded",
        summary="Known scanner noise only.",
        severity=LogAnalysisSeverity.INFO,
        fingerprints=_fingerprints({}),
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
                    "project_name": "demo-shop",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": LogSourceCollectionStatus.COLLECTED,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                        {
                            "source_key": "nginx",
                            "status": LogSourceCollectionStatus.COLLECTED,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ],
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = history_agent_factory(mcp_client, llm_provider)

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
    source_coverage = user_prompt["evidence"]["prompt_compacted"]["source_coverage"]
    assert source_coverage["available"] is True
    assert source_coverage["source_coverage_changed"] is True
    assert source_coverage["changed_sources"] == ["demo-shop.backend"]
    assert source_coverage["tool_scope_by_project"] == {}
    assert source_coverage["recommended_action"] == RecommendedAction.LLM_MAY_DECIDE
    assert user_prompt["current_coverage"] == {
        "zero_line_sources": [],
        "unavailable_sources": ["demo-shop.nginx"],
    }
    assert user_prompt["previous_analysis"]["coverage_snapshot"] == {
        "totals": {
            "project_count": 1,
            "source_count": 2,
            "zero_line_sources": 2,
        }
    }
    assert "projects" not in user_prompt["previous_analysis"]["coverage_snapshot"]
    assert user_prompt["evidence"]["prompt_compacted"]["grouped_error_diff"] is not None
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert user_prompt["final_report_allowed"] is True
    assert user_prompt["evidence_mode"] == "current_grouped_errors_available"


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_requires_tools_for_previous_warning(
    history_agent_factory: HistoryAgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Previous warning requires tools.",
                "severity": LogAnalysisSeverity.WARNING,
                "severity_rationale": "Prior warning should be verified.",
                "key_findings": ["Previous run had 500s."],
                "evidence": ["history_comparison.source_coverage required tools."],
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
        severity=LogAnalysisSeverity.WARNING,
        fingerprints=_fingerprints({"report": {"severity": LogAnalysisSeverity.WARNING}}),
        evidence_fingerprints=[
            "simulated:demo-shop.backend:http_500:count_7",
            "simulated:demo-shop.frontend:http_500:count_5",
        ],
        known_patterns=[],
        coverage_snapshot={
            "projects": [
                {
                    "project_name": "demo-shop",
                    "sources": [
                        {
                            "source_key": "backend",
                            "status": LogSourceCollectionStatus.COLLECTED,
                            "line_count": 120,
                            "zero_lines": False,
                        },
                        {
                            "source_key": "nginx",
                            "status": LogSourceCollectionStatus.UNAVAILABLE,
                            "line_count": 0,
                            "zero_lines": True,
                        },
                    ],
                }
            ]
        },
        fingerprint_version="log-analysis-fingerprint-v1",
    )
    agent = history_agent_factory(mcp_client, llm_provider)

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
    source_coverage = user_prompt["evidence"]["prompt_compacted"]["source_coverage"]
    assert source_coverage["available"] is True
    assert source_coverage["source_coverage_changed"] is False
    assert source_coverage["changed_sources"] == []
    assert source_coverage["tool_scope_by_project"] == {}
    assert source_coverage["recommended_action"] == RecommendedAction.LLM_MAY_DECIDE
    assert user_prompt["evidence"]["prompt_compacted"]["grouped_error_diff"] is not None
    assert user_prompt["next_required_action"] == LogAnalysisNextRequiredAction.CHOOSE_NEXT_ACTION
    assert user_prompt["final_report_allowed"] is True
    assert user_prompt["evidence_mode"] == "current_grouped_errors_available"


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_skips_duplicate_mcp_tool_calls(
    agent_factory: AgentFactory,
    mocker: MockerFixture,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    duplicate_action = {
        "action": LogAnalysisAllowedAction.CALL_TOOLS,
        "tool_calls": [
            {
                "tool_name": McpToolName.GROUP_ERRORS,
                "arguments": {"project_name": "demo-shop"},
            }
        ],
    }
    llm_provider.queue_text_response(json.dumps(duplicate_action))
    llm_provider.queue_text_response(json.dumps(duplicate_action))
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Logs were summarized after duplicate tool request was skipped.",
                "severity": LogAnalysisSeverity.INFO,
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
    agent = agent_factory(mcp_client, llm_provider)

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
        McpToolName.LIST_PROJECTS,
        "collect_logs:2026-05-19T00:00:00Z:2026-05-20T00:00:00Z",
        (
            "call_deterministic_tool:group_errors:{'project_name': 'demo-shop', "
            "'source_keys': ['backend']}"
        ),
        "call_deterministic_tool:group_errors:{'project_name': 'demo-shop'}",
    ]
    assert [result.tool_name for result in context.tool_results] == [
        McpToolName.GROUP_ERRORS,
        "duplicate_mcp_tool_call_skipped",
    ]
    duplicate_result = context.tool_results[1]
    assert duplicate_result.structured_content == {
        "action": "duplicate_mcp_tool_call_skipped",
        "tool_name": McpToolName.GROUP_ERRORS,
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
        "tool_name": McpToolName.GROUP_ERRORS,
    }


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_skips_same_scope_group_errors_with_different_limits(
    agent_factory: AgentFactory,
    mocker: MockerFixture,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {
                            "project_name": "demo-shop",
                            "source_key": "backend",
                            "max_groups": 50,
                        },
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {
                            "project_name": "demo-shop",
                            "source_key": "backend",
                            "max_groups": 200,
                        },
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(json.dumps(_final_report_payload()))
    info_mock = mocker.patch("agents.logger.info")
    agent = agent_factory(mcp_client, llm_provider)

    context = await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    assert (
        mcp_client.calls.count(
            "call_deterministic_tool:group_errors:{'project_name': 'demo-shop', "
            "'source_key': 'backend', 'max_groups': 50}"
        )
        == 1
    )
    assert all("'max_groups': 200" not in call for call in mcp_client.calls)
    assert [result.tool_name for result in context.tool_results] == [
        McpToolName.GROUP_ERRORS,
        "duplicate_mcp_tool_call_skipped",
    ]
    duplicate_log_calls = [
        call
        for call in info_mock.call_args_list
        if call.args and call.args[0] == "skipping duplicate LLM-requested MCP tool call"
    ]
    assert len(duplicate_log_calls) == 1


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_does_not_add_local_probe_interpretation(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results.update(
        {
            McpToolName.INSPECT_PROXY_ACTIVITY: {
                "action": McpToolName.INSPECT_PROXY_ACTIVITY,
                "project_name": "demo-shop",
                "total_requests": 100,
                "status_class_counts": {"2xx": 20, "4xx": 80, "5xx": 0},
                "upstream_error_count": 0,
            },
            "inspect_live_fail2ban_activity": {
                "action": "inspect_live_fail2ban_activity",
                "project_name": "host-security",
                "active_jails": 3,
                "currently_banned_total": 2,
            },
        }
    )
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.INSPECT_PROXY_ACTIVITY,
                        "arguments": {"project_name": "demo-shop"},
                    },
                    {
                        "tool_name": "inspect_live_fail2ban_activity",
                        "arguments": {"project_name": "host-security"},
                    },
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Scanner traffic is blocked.",
                "severity": LogAnalysisSeverity.INFO,
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
    agent = agent_factory(mcp_client, llm_provider)

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
        McpToolName.INSPECT_PROXY_ACTIVITY,
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
async def test_monitoring_workflow_agent_compacts_large_group_error_followup(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    groups: list[dict[str, Any]] = []
    for index in range(50):
        groups.append(
            {
                "fingerprint": f"nginx:http_4xx:404:/probe-{index}.php",
                "category": "http_4xx",
                "severity": "medium",
                "count": index + 1,
                "source_keys": ["nginx"],
                "request_paths": [f"/probe-{index}.php"],
                "status_codes": [404],
                "levels": [],
                "message_summary": "Grouped scanner probe",
                "first_timestamp": "2026-05-19T02:00:00Z",
                "last_timestamp": "2026-05-19T03:00:00Z",
                "example_lines": ["raw line payload that should stay out of prompts"] * 20,
            }
        )
    mcp_client.tool_results[McpToolName.GROUP_ERRORS] = {
        "action": McpToolName.GROUP_ERRORS,
        "project_name": "demo-shop",
        "grouped_error_count": len(groups),
        "matching_line_count": 1275,
        "truncated": False,
        "groups": groups,
    }
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {"project_name": "demo-shop", "max_groups": 200},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(json.dumps(_final_report_payload()))
    agent = agent_factory(mcp_client, llm_provider)

    context = await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    assert len(context.tool_results[0].structured_content["groups"]) == 50
    followup_text = cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    assert "raw line payload that should stay out of prompts" not in followup_text
    followup_payload = json.loads(followup_text)
    followup_content = followup_payload["tool_results"][0]["structured_content"]
    assert followup_content["prompt_compacted"] is True
    assert followup_content["grouped_error_count"] == 50
    assert followup_content["included_group_count"] == 20
    assert followup_content["omitted_group_count"] == 30
    assert followup_content["severity_counts"] == {"medium": 50}
    assert len(followup_content["groups"]) == 20


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_compacts_large_grep_followup(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results[McpToolName.GREP_LOG_SNAPSHOT] = {
        "action": McpToolName.GREP_LOG_SNAPSHOT,
        "project_name": "demo-shop",
        "source_key": "nginx",
        "grep": "/\\.env",
        "match_count": 50,
        "matches": [
            {
                "line_number": index + 1,
                "line": f"very long raw grep line {index} " + ("x" * 240),
            }
            for index in range(50)
        ],
    }
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
                            "grep": "/\\.env",
                            "max_matches": 100,
                        },
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(json.dumps(_final_report_payload()))
    agent = agent_factory(mcp_client, llm_provider)

    context = await agent.run_log_analysis(
        analysis_date=date(2026, 5, 19),
        log_window=LogCollectionWindow(
            since="2026-05-19T00:00:00Z",
            until="2026-05-20T00:00:00Z",
            since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
            until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
        ),
    )

    assert len(context.tool_results[0].structured_content["matches"]) == 50
    followup_payload = json.loads(
        cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    )
    structured_content = followup_payload["tool_results"][0]["structured_content"]
    assert structured_content["prompt_compacted"] is True
    assert structured_content["included_match_count"] == 20
    assert structured_content["omitted_match_count"] == 30
    assert len(structured_content["matches"]) == 20
    assert "very long raw grep line 49" not in json.dumps(structured_content)
    assert len(structured_content["matches"][0]["line"]) < 180


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_persists_llm_tool_usage_by_trace_id(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    mcp_client.tool_results.update(
        {
            McpToolName.INSPECT_PROXY_ACTIVITY: {
                "action": McpToolName.INSPECT_PROXY_ACTIVITY,
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
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.INSPECT_PROXY_ACTIVITY,
                        "arguments": {"project_name": "demo-shop"},
                    },
                    {
                        "tool_name": "inspect_live_fail2ban_activity",
                        "arguments": {"project_name": "host-security"},
                    },
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Scanner traffic is blocked.",
                "severity": LogAnalysisSeverity.INFO,
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
    agent = agent_factory(mcp_client, llm_provider)
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
    assert action_entries[0].action == LogAnalysisAllowedAction.CALL_TOOLS
    assert action_entries[0].llm_response_text
    llm_tool_calls = [step for step in steps if step.step_type == "mcp_tool_call"]
    assert [tool_call.tool_name for tool_call in llm_tool_calls] == [
        McpToolName.INSPECT_PROXY_ACTIVITY,
        "inspect_live_fail2ban_activity",
    ]
    assert all(tool_call.status == "succeeded" for tool_call in llm_tool_calls)
    assert all(tool_call.arguments_hash for tool_call in llm_tool_calls)


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_logs_llm_actions(
    agent_factory: AgentFactory,
    mocker: MockerFixture,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {"project_name": "demo-shop"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Logs were summarized.",
                "severity": LogAnalysisSeverity.INFO,
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
    agent = agent_factory(mcp_client, llm_provider)

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
    assert first_extra["action"] == LogAnalysisAllowedAction.CALL_TOOLS
    assert first_extra["requested_tool_names"] == [McpToolName.GROUP_ERRORS]
    assert first_extra["tool_call_count"] == 1
    assert first_extra["llm_response_text"] == (
        '{"action": "call_tools", "tool_calls": [{"tool_name": "group_errors", '
        '"arguments": {"project_name": "demo-shop"}}]}'
    )
    assert first_extra["llm_response_structured_output"] is None
    assert first_extra["llm_action_payload"]["tool_calls"][0]["arguments"] == {
        "project_name": "demo-shop"
    }
    second_extra = action_log_calls[1].kwargs["extra"]
    assert second_extra["iteration"] == 2
    assert second_extra["action"] == LogAnalysisAllowedAction.FINAL_REPORT
    assert second_extra["tool_call_count"] == 0
    assert second_extra["final_report_severity"] == LogAnalysisSeverity.INFO
    assert second_extra["final_report_key_finding_count"] == 1
    assert '"action": "final_report"' in second_extra["llm_response_text"]
    assert second_extra["llm_response_structured_output"] is None


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_reads_optional_skills(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.READ_SKILLS,
                "skill_names": ["bot_detection"],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Logs were summarized with bot guidance.",
                "severity": LogAnalysisSeverity.INFO,
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
    agent = agent_factory(mcp_client, llm_provider)

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
    assert context.tool_results[0].tool_name == LogAnalysisAllowedAction.READ_SKILLS
    assert context.tool_results[0].structured_content["skills"][0]["skill_name"] == (
        "bot_detection"
    )
    followup_text: str = cast(TextPart, llm_provider.requests[1].messages[-1].parts[0]).text
    assert "Bot detection skill body." in followup_text


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_rejects_unavailable_skill_reads(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.READ_SKILLS,
                "skill_names": ["severity_guide"],
            }
        )
    )
    agent = agent_factory(mcp_client, llm_provider)

    with pytest.raises(
        LogAnalysisAgentError,
        match="requested unavailable optional skill",
    ) as error_info:
        await agent.run_log_analysis(
            analysis_date=date(2026, 5, 19),
            log_window=LogCollectionWindow(
                since="2026-05-19T00:00:00Z",
                until="2026-05-20T00:00:00Z",
                since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
                until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
            ),
        )
    assert isinstance(error_info.value.__cause__, ValueError)
    assert error_info.value.collect_logs is not None


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_records_llm_report_time(
    agent_factory: AgentFactory,
    mocker: MockerFixture,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": McpToolName.GROUP_ERRORS,
                        "arguments": {"project_name": "demo-shop"},
                    }
                ],
            }
        )
    )
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Logs were summarized.",
                "severity": LogAnalysisSeverity.INFO,
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
    mocker.patch("agents.monotonic", side_effect=[50.0, 51.0, 54.321])
    agent = agent_factory(mcp_client, llm_provider)

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
async def test_monitoring_workflow_agent_rejects_unknown_tool_requests(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.CALL_TOOLS,
                "tool_calls": [
                    {
                        "tool_name": "delete_everything",
                        "arguments": {},
                    }
                ],
            }
        )
    )
    agent = agent_factory(mcp_client, llm_provider)

    with pytest.raises(LogAnalysisAgentError, match="requested unavailable MCP tool") as error_info:
        await agent.run_log_analysis(
            analysis_date=date(2026, 5, 19),
            log_window=LogCollectionWindow(
                since="2026-05-19T00:00:00Z",
                until="2026-05-20T00:00:00Z",
                since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
                until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
            ),
        )
    assert isinstance(error_info.value.__cause__, ValueError)
    assert error_info.value.collect_logs is not None


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_rejects_invalid_final_report(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClient()
    llm_provider = MockProvider()
    llm_provider.queue_text_response(
        json.dumps(
            {
                "action": LogAnalysisAllowedAction.FINAL_REPORT,
                "summary": "Missing required fields.",
                "severity": "NOTICE",
            }
        )
    )
    agent = agent_factory(mcp_client, llm_provider)

    with pytest.raises(
        LogAnalysisAgentError,
        match="LLM final report did not match expected shape",
    ) as error_info:
        await agent.run_log_analysis(
            analysis_date=date(2026, 5, 19),
            log_window=LogCollectionWindow(
                since="2026-05-19T00:00:00Z",
                until="2026-05-20T00:00:00Z",
                since_datetime=datetime(2026, 5, 19, tzinfo=UTC),
                until_datetime=datetime(2026, 5, 20, tzinfo=UTC),
            ),
        )
    assert isinstance(error_info.value.__cause__, ValueError)
    assert error_info.value.collect_logs is not None


@pytest.mark.asyncio
async def test_monitoring_workflow_agent_stops_when_mcp_has_no_projects(
    agent_factory: AgentFactory,
) -> None:
    mcp_client = FakeMcpWorkflowClientWithoutProjects()
    agent = agent_factory(mcp_client, MockProvider())

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
    assert error_info.value.tool_name == McpToolName.LIST_PROJECTS
    assert mcp_client.calls == [
        "get_workflow_bundle",
        "read_resource:skill://workflow/severity_guide",
        McpToolName.LIST_PROJECTS,
    ]
