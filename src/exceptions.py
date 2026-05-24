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
