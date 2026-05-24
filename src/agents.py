from __future__ import annotations

import json
from datetime import date
from time import monotonic
from typing import Any

from llm_core.exceptions import StructuredOutputError
from llm_core.protocols import LLMProvider
from llm_core.types import GenerationOptions, LLMRequest, LLMResponse, Message, ResponseFormat
from pydantic import ValidationError

from assets_loader import load_markdown_bullets, load_markdown_mapping
from exceptions import McpClientError
from logging_config import get_logger
from mcp import McpWorkflowClient
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogAnalysisFinalReport,
    LogAnalysisPreparedPrompt,
    LogAnalysisPromptContext,
    LogAnalysisSkillReadRequest,
    LogAnalysisToolCallRequest,
    LogAnalysisToolResult,
    LogCollectionWindow,
    ProjectManifestSummary,
    SnapshotAccessGuidance,
    WorkflowBootstrap,
    WorkflowSkill,
    WorkflowSkillContent,
)

logger = get_logger(__name__)
MAX_LLM_TOOL_LOOP_ITERATIONS = 5
LOG_ANALYSIS_INSTRUCTIONS = load_markdown_bullets("log_analysis_instructions.md")
LOG_ANALYSIS_FOLLOWUP_INSTRUCTIONS = load_markdown_bullets("log_analysis_followup_instructions.md")
LOG_ANALYSIS_REPORT_CONTRACT = load_markdown_mapping("log_analysis_report_contract.md")


class MonitoringWorkflowAgent:
    """Agent boundary for MCP-backed monitoring workflow bootstrap calls."""

    def __init__(
        self,
        mcp_client: McpWorkflowClient,
        llm_provider: LLMProvider,
        private_monitoring_context: str,
    ) -> None:
        self.mcp_client = mcp_client
        self.llm_provider = llm_provider
        self.private_monitoring_context = private_monitoring_context

    async def run_log_analysis(
        self,
        *,
        analysis_date: date,
        log_window: LogCollectionWindow,
    ) -> LogAnalysisAgentContext:
        """Prepare deterministic context before the first log-analysis LLM call."""

        logger.info(
            "loading MCP daily log workflow bundle",
            extra={"event": "workflow_bundle_load_start"},
        )
        workflow: WorkflowBootstrap = await self.mcp_client.get_workflow_bundle()
        mandatory_skills: list[WorkflowSkillContent] = await self._read_mandatory_skills(
            workflow.mandatory_skills
        )
        available_projects: list[ProjectManifestSummary] = await self.mcp_client.list_projects()
        if not available_projects:
            raise McpClientError(
                (
                    "MCP list_projects returned no projects for this workflow caller. "
                    "Upload project manifests to MCP or check the caller project scope "
                    "before collecting logs."
                ),
                mcp_url=self.mcp_client.base_url,
                tool_name="list_projects",
            )
        collect_logs: CollectLogsArtifact = await self.mcp_client.collect_logs(
            since=log_window.since,
            until=log_window.until,
        )
        prompt: LogAnalysisPreparedPrompt = self._build_log_analysis_prompt(
            analysis_date=analysis_date,
            workflow=workflow,
            mandatory_skills=mandatory_skills,
            available_projects=available_projects,
            collect_logs=collect_logs,
            private_monitoring_context=self.private_monitoring_context,
        )
        llm_report_started_at: float = monotonic()
        final_report: LogAnalysisFinalReport
        tool_results: list[LogAnalysisToolResult]
        llm_tokens_used: int
        llm_cost_usd: float
        final_report, tool_results, llm_tokens_used, llm_cost_usd = await self._run_tool_loop(
            prompt=prompt,
            workflow=workflow,
            analysis_date=analysis_date,
        )
        llm_report_execution_time_seconds: float = round(monotonic() - llm_report_started_at, 3)
        logger.info(
            "completed log-analysis LLM tool loop",
            extra={
                "event": "log_analysis_llm_final_report_done",
                "workflow_name": workflow.workflow_name,
                "mandatory_skill_count": len(workflow.mandatory_skills),
                "optional_skill_count": len(workflow.optional_skills),
                "tool_count": len(workflow.tools),
                "available_project_count": len(available_projects),
                "collected_project_count": len(collect_logs.projects),
                "tool_result_count": len(tool_results),
                "log_window_since": log_window.since,
                "log_window_until": log_window.until,
                "severity": final_report.severity,
                "llm_report_execution_time_seconds": llm_report_execution_time_seconds,
            },
        )
        return LogAnalysisAgentContext(
            workflow=workflow,
            collect_logs=collect_logs,
            prompt=prompt,
            tool_results=tool_results,
            final_report=final_report,
            log_window_since=log_window.since_datetime,
            log_window_until=log_window.until_datetime,
            llm_tokens_used=llm_tokens_used,
            llm_cost_usd=llm_cost_usd,
            llm_report_execution_time_seconds=llm_report_execution_time_seconds,
        )

    async def _read_mandatory_skills(
        self,
        skills: list[WorkflowSkill],
    ) -> list[WorkflowSkillContent]:
        """Fetch mandatory workflow skill resources before the first LLM call."""

        skill_contents: list[WorkflowSkillContent] = []
        for skill in skills:
            content: str = await self.mcp_client.read_resource(skill.resource_uri)
            skill_contents.append(
                WorkflowSkillContent(
                    name=skill.name,
                    resource_uri=skill.resource_uri,
                    description=skill.description,
                    content=content,
                )
            )
        return skill_contents

    @staticmethod
    def _build_log_analysis_prompt(
        *,
        analysis_date: date,
        workflow: WorkflowBootstrap,
        mandatory_skills: list[WorkflowSkillContent],
        available_projects: list[ProjectManifestSummary],
        collect_logs: CollectLogsArtifact,
        private_monitoring_context: str,
    ) -> LogAnalysisPreparedPrompt:
        """Build the structured prompt context that will be sent to the LLM later."""

        return LogAnalysisPreparedPrompt(
            system_prompt=MonitoringWorkflowAgent._build_system_prompt_with_mandatory_skills(
                workflow=workflow,
                mandatory_skills=mandatory_skills,
                private_monitoring_context=private_monitoring_context,
            ),
            context=LogAnalysisPromptContext(
                analysis_date=analysis_date,
                workflow_name=workflow.workflow_name,
                current_phase="inspect_collected_logs",
                completed_steps=[
                    "analyze_daily_log_bundle",
                    "read_mandatory_skills",
                    "list_projects",
                    "collect_logs",
                ],
                allowed_actions=["call_tools", "read_skills", "final_report"],
                next_required_action="call_tools",
                final_report_allowed=False,
                available_projects=available_projects,
                mandatory_skills=mandatory_skills,
                optional_skills=workflow.optional_skills,
                collection=collect_logs,
                snapshot_access=SnapshotAccessGuidance(
                    workspace=collect_logs.workspace,
                    session_id=collect_logs.session_id,
                    session_id_is_for_session_workspace_only=True,
                    workflow_followup_arguments=["project_name", "archive_name"],
                    instruction=(
                        "This collection is a workflow snapshot. Use project_name for "
                        "workflow follow-up tools. Ignore session_id unless a later "
                        "collection explicitly uses workspace='session'."
                    ),
                ),
                available_tools=workflow.tools,
                report_contract=LOG_ANALYSIS_REPORT_CONTRACT,
                instructions=LOG_ANALYSIS_INSTRUCTIONS,
            ),
        )

    @staticmethod
    def _build_system_prompt_with_mandatory_skills(
        *,
        workflow: WorkflowBootstrap,
        mandatory_skills: list[WorkflowSkillContent],
        private_monitoring_context: str,
    ) -> str:
        """Append private VPS context and mandatory skills to the MCP-owned prompt."""

        skill_sections: list[str] = []
        for skill in mandatory_skills:
            skill_sections.append(
                "\n".join(
                    [
                        f"## {skill.name}",
                        "",
                        skill.content,
                    ]
                )
            )
        mandatory_skill_prompt: str = "\n\n".join(skill_sections)
        return "\n\n".join(
            part
            for part in [
                workflow.prompt.strip(),
                "# Mandatory Workflow Skills",
                mandatory_skill_prompt.strip(),
                "# Private Monitoring Context",
                private_monitoring_context.strip(),
            ]
            if part
        )

    async def _run_tool_loop(
        self,
        *,
        prompt: LogAnalysisPreparedPrompt,
        workflow: WorkflowBootstrap,
        analysis_date: date,
    ) -> tuple[LogAnalysisFinalReport, list[LogAnalysisToolResult], int, float]:
        """Run the LLM action loop until a final report is produced."""

        messages: list[Message] = [
            Message.from_text("system", prompt.system_prompt),
            Message.from_text("user", prompt.user_prompt),
        ]
        tool_results: list[LogAnalysisToolResult] = []
        fetched_skill_names: set[str] = set()
        llm_tokens_used: int = 0
        llm_cost_usd: float = 0.0
        for iteration in range(1, MAX_LLM_TOOL_LOOP_ITERATIONS + 1):
            llm_response: LLMResponse = self._request_llm_action(
                messages=messages,
                workflow=workflow,
                analysis_date=analysis_date,
                iteration=iteration,
            )
            if llm_response.usage is not None:
                llm_tokens_used += llm_response.usage.total_tokens
                if llm_response.usage.cost_usd is not None:
                    llm_cost_usd += llm_response.usage.cost_usd

            payload: dict[str, Any] = self._extract_llm_payload(llm_response)
            action: object = payload.get("action")
            self._log_llm_action_payload(
                response=llm_response,
                payload=payload,
                workflow=workflow,
                iteration=iteration,
            )
            if action == "final_report":
                final_report: LogAnalysisFinalReport = self._build_final_report_payload(payload)
                return final_report, tool_results, llm_tokens_used, llm_cost_usd
            if action == "call_tools":
                tool_request: LogAnalysisToolCallRequest = self._build_tool_call_request(payload)
                new_tool_results: list[LogAnalysisToolResult] = await self._execute_requested_tools(
                    tool_request=tool_request,
                    workflow=workflow,
                )
            elif action == "read_skills":
                skill_request: LogAnalysisSkillReadRequest = self._build_skill_read_request(payload)
                new_tool_results = await self._execute_requested_skill_reads(
                    skill_request=skill_request,
                    workflow=workflow,
                    fetched_skill_names=fetched_skill_names,
                )
            else:
                raise ValueError("LLM action did not match expected shape.")

            tool_results.extend(new_tool_results)
            messages.append(
                Message.from_text(
                    "user",
                    json.dumps(
                        {
                            "previous_action": payload,
                            "tool_results": [
                                tool_result.model_dump(mode="json")
                                for tool_result in new_tool_results
                            ],
                            "instructions": LOG_ANALYSIS_FOLLOWUP_INSTRUCTIONS,
                        },
                        indent=2,
                    ),
                )
            )

        raise ValueError("LLM tool loop exceeded maximum iterations before final_report.")

    def _request_llm_action(
        self,
        *,
        messages: list[Message],
        workflow: WorkflowBootstrap,
        analysis_date: date,
        iteration: int,
    ) -> LLMResponse:
        """Ask the configured LLM provider for the next JSON workflow action."""

        request: LLMRequest = LLMRequest(
            messages=tuple(messages),
            options=GenerationOptions(
                temperature=0.0,
                response_format=ResponseFormat.JSON_OBJECT,
            ),
            metadata={
                "workflow_name": workflow.workflow_name,
                "analysis_date": analysis_date.isoformat(),
                "phase": "log_analysis_2b",
                "iteration": str(iteration),
            },
        )
        logger.info(
            "calling LLM for log-analysis workflow action",
            extra={
                "event": "log_analysis_llm_action_start",
                "workflow_name": workflow.workflow_name,
                "iteration": iteration,
            },
        )
        return self.llm_provider.generate(request)

    @staticmethod
    def _log_llm_action_payload(
        *,
        response: LLMResponse,
        payload: dict[str, Any],
        workflow: WorkflowBootstrap,
        iteration: int,
    ) -> None:
        """Log the LLM action payload between tool-loop iterations."""

        action: object = payload.get("action")
        tool_calls: object = payload.get("tool_calls")
        skill_names: object = payload.get("skill_names")
        requested_tool_names: list[str] = []
        if isinstance(tool_calls, list):
            requested_tool_names = [
                str(tool_call["tool_name"])
                for tool_call in tool_calls
                if isinstance(tool_call, dict) and "tool_name" in tool_call
            ]
        extra: dict[str, Any] = {
            "event": "log_analysis_llm_action_received",
            "workflow_name": workflow.workflow_name,
            "iteration": iteration,
            "action": action,
            "requested_tool_names": requested_tool_names,
            "requested_skill_names": skill_names if isinstance(skill_names, list) else [],
            "tool_call_count": len(requested_tool_names),
            "llm_response_text": response.text,
            "llm_response_structured_output": response.structured_output,
            "llm_action_payload": payload,
        }
        if action == "final_report":
            key_findings: object = payload.get("key_findings")
            extra["final_report_severity"] = payload.get("severity")
            extra["final_report_key_finding_count"] = (
                len(key_findings) if isinstance(key_findings, list) else 0
            )
        logger.info("received LLM workflow action", extra=extra)

    async def _execute_requested_tools(
        self,
        *,
        tool_request: LogAnalysisToolCallRequest,
        workflow: WorkflowBootstrap,
    ) -> list[LogAnalysisToolResult]:
        """Execute validated MCP tools requested by the LLM action."""

        available_tool_names: set[str] = {tool.tool_name for tool in workflow.tools}
        tool_results: list[LogAnalysisToolResult] = []
        if not tool_request.tool_calls:
            raise ValueError("LLM call_tools action did not include any tool calls.")
        for tool_call in tool_request.tool_calls:
            if tool_call.tool_name not in available_tool_names:
                raise ValueError(f"LLM requested unavailable MCP tool: {tool_call.tool_name}")
            structured_content: dict[str, Any] = await self.mcp_client.call_deterministic_tool(
                tool_call.tool_name,
                tool_call.arguments,
            )
            tool_results.append(
                LogAnalysisToolResult(
                    tool_name=tool_call.tool_name,
                    arguments=tool_call.arguments,
                    structured_content=structured_content,
                )
            )
        return tool_results

    async def _execute_requested_skill_reads(
        self,
        *,
        skill_request: LogAnalysisSkillReadRequest,
        workflow: WorkflowBootstrap,
        fetched_skill_names: set[str],
    ) -> list[LogAnalysisToolResult]:
        """Read optional MCP workflow skill resources requested by the LLM action."""

        optional_skills_by_name: dict[str, WorkflowSkill] = {
            skill.name: skill for skill in workflow.optional_skills
        }
        if not skill_request.skill_names:
            raise ValueError("LLM read_skills action did not include any skill names.")

        skill_contents: list[dict[str, str]] = []
        for skill_name in skill_request.skill_names:
            skill: WorkflowSkill | None = optional_skills_by_name.get(skill_name)
            if skill is None:
                raise ValueError(f"LLM requested unavailable optional skill: {skill_name}")
            if skill.name in fetched_skill_names:
                raise ValueError(f"LLM requested already fetched optional skill: {skill.name}")
            content: str = await self.mcp_client.read_resource(skill.resource_uri)
            fetched_skill_names.add(skill.name)
            skill_contents.append(
                {
                    "skill_name": skill.name,
                    "resource_uri": skill.resource_uri,
                    "description": skill.description,
                    "content": content,
                }
            )

        return [
            LogAnalysisToolResult(
                tool_name="read_skills",
                arguments={"skill_names": skill_request.skill_names},
                structured_content={
                    "action": "read_skills",
                    "skills": skill_contents,
                },
            )
        ]

    @staticmethod
    def _extract_llm_payload(response: LLMResponse) -> dict[str, Any]:
        """Extract a JSON object payload from an LLM response."""

        payload: Any = response.structured_output
        if payload is None and response.text is not None:
            try:
                payload = json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise ValueError("LLM action response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("LLM action response must be a JSON object.")
        return payload

    @staticmethod
    def _build_tool_call_request(payload: dict[str, Any]) -> LogAnalysisToolCallRequest:
        """Validate an LLM call_tools action."""

        try:
            return LogAnalysisToolCallRequest.model_validate(payload)
        except (TypeError, ValidationError, StructuredOutputError) as exc:
            raise ValueError("LLM tool request did not match expected shape.") from exc

    @staticmethod
    def _build_skill_read_request(payload: dict[str, Any]) -> LogAnalysisSkillReadRequest:
        """Validate an LLM read_skills action."""

        try:
            return LogAnalysisSkillReadRequest.model_validate(payload)
        except (TypeError, ValidationError, StructuredOutputError) as exc:
            raise ValueError("LLM skill read request did not match expected shape.") from exc

    @staticmethod
    def _build_final_report_payload(payload: dict[str, Any]) -> LogAnalysisFinalReport:
        """Validate a final report payload returned by the LLM provider."""

        try:
            return LogAnalysisFinalReport.model_validate(payload)
        except (TypeError, ValidationError, StructuredOutputError) as exc:
            raise ValueError("LLM final report did not match expected shape.") from exc
