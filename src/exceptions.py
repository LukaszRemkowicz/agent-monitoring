from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas import CollectLogsArtifact, LogAnalysisPreparedPrompt, WorkflowBootstrap


def format_exception_chain(exc: BaseException) -> str:
    """Return a compact message with chained exception details."""

    messages: list[str] = [str(exc)]
    current: BaseException | None = exc.__cause__ or exc.__context__
    while current is not None:
        current_message: str = str(current)
        current_type: str = current.__class__.__name__
        if current_message:
            messages.append(f"{current_type}: {current_message}")
        else:
            messages.append(current_type)
        current = current.__cause__ or current.__context__
    return "\nCaused by: ".join(messages)


class McpClientError(RuntimeError):
    """Raised when an MCP JSON-RPC call cannot complete or validate."""

    def __init__(
        self,
        message: str,
        *,
        mcp_url: str = "",
        tool_name: str = "",
        hint: str = "",
    ) -> None:
        super().__init__(message)
        self.mcp_url = mcp_url
        self.tool_name = tool_name
        self.hint = hint


class PrivateMonitoringContextError(RuntimeError):
    """Raised when mandatory private monitoring context is missing or invalid."""

    def __init__(self, message: str, *, context_path: str = "") -> None:
        super().__init__(message)
        self.context_path = context_path


class LogAnalysisAgentError(RuntimeError):
    """Raised when the agent fails after collecting partial workflow context."""

    def __init__(
        self,
        message: str,
        *,
        workflow: WorkflowBootstrap | None = None,
        collect_logs: CollectLogsArtifact | None = None,
        prompt: LogAnalysisPreparedPrompt | None = None,
    ) -> None:
        super().__init__(message)
        self.workflow = workflow
        self.collect_logs = collect_logs
        self.prompt = prompt
