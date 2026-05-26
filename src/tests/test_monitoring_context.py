from pathlib import Path

import pytest

from utils.monitoring_context import load_private_monitoring_context


def test_load_private_monitoring_context_reads_local_file(tmp_path: Path) -> None:
    context_path: Path = tmp_path / "vps_monitoring_context.md"
    context_path.write_text(
        "# Private VPS Context\n\nInstalled services: portfolio, mcp, monitoring.",
        encoding="utf-8",
    )

    context: str = load_private_monitoring_context(context_path)

    assert context == "# Private VPS Context\n\nInstalled services: portfolio, mcp, monitoring."


def test_load_private_monitoring_context_raises_when_file_is_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="Private monitoring context file is required"):
        load_private_monitoring_context(tmp_path / "missing.md")


def test_load_private_monitoring_context_raises_when_file_is_empty(tmp_path: Path) -> None:
    context_path: Path = tmp_path / "vps_monitoring_context.md"
    context_path.write_text("\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Private monitoring context file is empty"):
        load_private_monitoring_context(context_path)
