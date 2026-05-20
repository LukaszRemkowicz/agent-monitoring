from __future__ import annotations

from datetime import date
from typing import Protocol

from schemas import McpServiceStatus, WorkflowBootstrap


class WorkflowBundleClient(Protocol):
    async def get_workflow_bundle(self) -> WorkflowBootstrap: ...


class LogAnalysisAgent(Protocol):
    async def run_log_analysis(self) -> WorkflowBootstrap: ...


class McpStatusClient(Protocol):
    async def get_service_status(self) -> McpServiceStatus: ...


class LogAnalysisReader(Protocol):
    async def get_by_date(self, analysis_date: date) -> object | None: ...
