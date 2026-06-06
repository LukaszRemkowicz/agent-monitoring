from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import pytest_asyncio
from llm_core.providers.mock import MockProvider
from tortoise import Tortoise

from agents import MonitoringWorkflowAgent
from conf import Settings, set_settings, settings
from mcp import McpWorkflowClient
from schemas import CollectLogsArtifact, LogSourceCollectionStatus, LogWorkspace, McpToolName
from services.log_history_comparison import LogAnalysisHistoryComparisonService

PRIVATE_MONITORING_CONTEXT = (
    "# Private VPS Monitoring Context\n\n"
    "Installed services: landingpage, vps-security, mcp-log-server."
)

AgentFactory = Callable[[McpWorkflowClient, MockProvider], MonitoringWorkflowAgent]
HistoryAgentFactory = Callable[[McpWorkflowClient, MockProvider], MonitoringWorkflowAgent]


@contextmanager
def override_settings(**updates: object) -> Iterator[Settings]:
    """Temporarily patch selected shared app settings for tests."""

    previous_settings = settings.copy()
    effective_settings = previous_settings.copy(**updates)
    set_settings(effective_settings)
    try:
        yield effective_settings
    finally:
        set_settings(previous_settings)


def build_collect_logs_artifact_payload(
    *,
    since: str = "2026-05-19T00:00:00Z",
    until: str = "2026-05-20T00:00:00Z",
    session_id: str | None = None,
    requested_project_names: list[str] | None = None,
    next_step_tips: list[str] | None = None,
    warnings: list[str] | None = None,
    resolved_source_keys: list[str] | None = None,
    include_unavailable_nginx: bool = False,
) -> dict[str, Any]:
    """Return the canonical collect_logs artifact payload used by tests."""

    effective_resolved_source_keys: list[str] = (
        ["backend", "nginx"] if resolved_source_keys is None else resolved_source_keys
    )
    sources: list[dict[str, Any]] = [
        {
            "source_key": "backend",
            "source_type": "docker",
            "target": "backend",
            "description": "Backend app logs",
            "stream": "stdout",
            "status": LogSourceCollectionStatus.COLLECTED,
            "line_count": 120,
            "byte_count": 4096,
            "output_file": "workflow/landingpage/latest/backend.log",
            "error": None,
            "retry_tips": [],
        }
    ]
    if include_unavailable_nginx:
        sources.append(
            {
                "source_key": "nginx",
                "source_type": "file",
                "target": "/var/log/nginx/access.log",
                "description": "Nginx access logs",
                "stream": None,
                "status": LogSourceCollectionStatus.UNAVAILABLE,
                "line_count": 0,
                "byte_count": 0,
                "output_file": None,
                "error": "file missing",
                "retry_tips": ["Check nginx log mount."],
            }
        )

    return {
        "action": McpToolName.COLLECT_LOGS,
        "workspace": LogWorkspace.WORKFLOW,
        "session_id": session_id,
        "requested_project_names": requested_project_names or ["landingpage"],
        "next_step_tips": (
            ["Use group_snapshot_errors next."] if next_step_tips is None else next_step_tips
        ),
        "projects": [
            {
                "requested_project_name": "landingpage",
                "project_name": "landingpage",
                "workspace": LogWorkspace.WORKFLOW,
                "snapshot_dir": "workflow/landingpage/latest",
                "requested_source_keys": ["all"],
                "requested_since": since,
                "requested_until": until,
                "warnings": [] if warnings is None else warnings,
                "retry_tips": [],
                "unknown_requested_source_keys": [],
                "resolved_source_keys": effective_resolved_source_keys,
                "collected_at": "2026-05-20T00:01:00Z",
                "sources": sources,
            }
        ],
    }


@pytest.fixture
def collect_logs_artifact() -> CollectLogsArtifact:
    """Return the canonical validated collect_logs artifact used by tests."""

    return CollectLogsArtifact.model_validate(build_collect_logs_artifact_payload())


@pytest.fixture
def agent_factory() -> AgentFactory:
    """Build a default log-analysis agent for tests without history comparison."""

    def create_agent(
        mcp_client: McpWorkflowClient,
        llm_provider: MockProvider,
    ) -> MonitoringWorkflowAgent:
        return MonitoringWorkflowAgent(
            mcp_client,
            llm_provider=llm_provider,
            private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
        )

    return create_agent


@pytest.fixture
def history_agent_factory() -> HistoryAgentFactory:
    """Build a log-analysis agent with history comparison enabled for tests."""

    def create_agent(
        mcp_client: McpWorkflowClient,
        llm_provider: MockProvider,
    ) -> MonitoringWorkflowAgent:
        return MonitoringWorkflowAgent(
            mcp_client,
            llm_provider=llm_provider,
            private_monitoring_context=PRIVATE_MONITORING_CONTEXT,
            history_comparison_service=LogAnalysisHistoryComparisonService(),
            history_comparison_enabled=True,
        )

    return create_agent


@pytest_asyncio.fixture(autouse=True)
async def tortoise_db() -> AsyncIterator[None]:
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": ["db.models"],
                    "default_connection": "default",
                }
            },
        }
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()
