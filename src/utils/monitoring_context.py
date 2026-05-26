"""Private monitoring context loading for LLM prompts."""

from __future__ import annotations

from pathlib import Path

from exceptions import PrivateMonitoringContextError


def load_private_monitoring_context(path: Path | str) -> str:
    """Load local VPS context that is intentionally kept outside Git."""

    context_path: Path = Path(path)
    try:
        context: str = context_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise PrivateMonitoringContextError(
            f"Private monitoring context file is required but was not found: {context_path}",
            context_path=str(context_path),
        ) from None
    if not context:
        raise PrivateMonitoringContextError(
            f"Private monitoring context file is empty: {context_path}",
            context_path=str(context_path),
        )
    return context
