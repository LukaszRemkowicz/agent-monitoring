from __future__ import annotations

import json
from datetime import date
from time import monotonic
from typing import Any

from llm_core.exceptions import StructuredOutputError
from llm_core.protocols import LLMProvider
from llm_core.types import GenerationOptions, LLMRequest, LLMResponse, Message, ResponseFormat
from pydantic import ValidationError

from exceptions import McpClientError
from logging_config import get_logger
from mcp import McpWorkflowClient
from schemas import (
    CollectLogsArtifact,
    LogAnalysisAgentContext,
    LogAnalysisFinalReport,
    LogAnalysisPreparedPrompt,
    LogAnalysisPromptContext,
    LogCollectionWindow,
    ProjectManifestSummary,
    SnapshotAccessGuidance,
    WorkflowBootstrap,
    WorkflowSkill,
    WorkflowSkillContent,
)

logger = get_logger(__name__)


class MonitoringWorkflowAgent:
    """Agent boundary for MCP-backed monitoring workflow bootstrap calls."""

    def __init__(self, mcp_client: McpWorkflowClient, llm_provider: LLMProvider) -> None:
        self.mcp_client = mcp_client
        self.llm_provider = llm_provider

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
        )
        final_report: LogAnalysisFinalReport
        llm_response: LLMResponse
        llm_report_started_at: float = monotonic()
        llm_response = self._request_final_report(
            prompt=prompt,
            workflow=workflow,
            analysis_date=analysis_date,
        )
        final_report = self._build_final_report(llm_response)
        llm_report_execution_time_seconds: float = round(monotonic() - llm_report_started_at, 3)
        logger.info(
            "completed one-shot log-analysis LLM report",
            extra={
                "event": "log_analysis_llm_final_report_done",
                "workflow_name": workflow.workflow_name,
                "mandatory_skill_count": len(workflow.mandatory_skills),
                "optional_skill_count": len(workflow.optional_skills),
                "tool_count": len(workflow.tools),
                "available_project_count": len(available_projects),
                "collected_project_count": len(collect_logs.projects),
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
            final_report=final_report,
            log_window_since=log_window.since_datetime,
            log_window_until=log_window.until_datetime,
            llm_tokens_used=llm_response.usage.total_tokens if llm_response.usage else 0,
            llm_cost_usd=(
                llm_response.usage.cost_usd
                if llm_response.usage and llm_response.usage.cost_usd is not None
                else 0.0
            ),
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
    ) -> LogAnalysisPreparedPrompt:
        """Build the structured prompt context that will be sent to the LLM later."""

        return LogAnalysisPreparedPrompt(
            system_prompt=MonitoringWorkflowAgent._build_system_prompt_with_mandatory_skills(
                workflow=workflow,
                mandatory_skills=mandatory_skills,
            ),
            context=LogAnalysisPromptContext(
                analysis_date=analysis_date,
                workflow_name=workflow.workflow_name,
                current_phase="final_report",
                completed_steps=[
                    "analyze_daily_log_bundle",
                    "read_mandatory_skills",
                    "list_projects",
                    "collect_logs",
                ],
                allowed_actions=["call_tools", "final_report"],
                next_required_action="final_report",
                final_report_allowed=True,
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
                report_contract={
                    "summary": "Brief overview of the day's log health.",
                    "severity": "INFO|WARNING|CRITICAL",
                    "key_findings": "List of specific findings.",
                    "recommendations": "Concrete next steps.",
                    "trend_summary": "Comparison against historical context when available.",
                },
                instructions=[
                    "Phase 2A is a one-shot final report without follow-up tool calls.",
                    "Use only the deterministic MCP artifact already included in this prompt.",
                    "Zero collected log lines are not evidence that a service is healthy.",
                    (
                        "When line_count is 0, say no log lines were collected for that source; "
                        "do not claim no errors, no failures, or healthy service behavior."
                    ),
                    "Use source_key names exactly as MCP reports them.",
                    "Do not invent container, service, or project names.",
                    (
                        "Distinguish collected, unavailable, and zero-line sources in the "
                        "summary and recommendations."
                    ),
                    "Do not invent raw log facts.",
                    "Anchor severity to the collected 24h window.",
                    "Borrow the landingpage monitoring report contract.",
                ],
            ),
        )

    @staticmethod
    def _build_system_prompt_with_mandatory_skills(
        *,
        workflow: WorkflowBootstrap,
        mandatory_skills: list[WorkflowSkillContent],
    ) -> str:
        """Append mandatory workflow skill text to the MCP-owned system prompt."""

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
            ]
            if part
        )

    def _request_final_report(
        self,
        *,
        prompt: LogAnalysisPreparedPrompt,
        workflow: WorkflowBootstrap,
        analysis_date: date,
    ) -> LLMResponse:
        """Ask the configured LLM provider for a one-shot final JSON report."""

        request: LLMRequest = LLMRequest(
            messages=(
                Message.from_text("system", prompt.system_prompt),
                Message.from_text("user", prompt.user_prompt),
            ),
            options=GenerationOptions(
                temperature=0.0,
                response_format=ResponseFormat.JSON_OBJECT,
            ),
            metadata={
                "workflow_name": workflow.workflow_name,
                "analysis_date": analysis_date.isoformat(),
                "phase": "log_analysis_2a",
            },
        )
        logger.info(
            "calling LLM for one-shot log-analysis report",
            extra={
                "event": "log_analysis_llm_final_report_start",
                "workflow_name": workflow.workflow_name,
            },
        )
        return self.llm_provider.generate(request)

    @staticmethod
    def _build_final_report(response: LLMResponse) -> LogAnalysisFinalReport:
        """Validate the final report returned by the LLM provider."""

        report_payload: Any = response.structured_output
        if report_payload is None and response.text is not None:
            try:
                report_payload = json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise ValueError("LLM final report was not valid JSON.") from exc
        try:
            return LogAnalysisFinalReport.model_validate(report_payload)
        except (TypeError, ValidationError, StructuredOutputError) as exc:
            raise ValueError("LLM final report did not match expected shape.") from exc
