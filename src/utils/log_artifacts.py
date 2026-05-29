from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def collect_log_artifact_byte_count(artifact: Mapping[str, Any]) -> int:
    collect_logs = artifact.get("collect_logs")
    if isinstance(collect_logs, Mapping):
        artifact = collect_logs

    total = 0
    projects = artifact.get("projects", [])
    if not isinstance(projects, list):
        return total

    for project in projects:
        if not isinstance(project, Mapping):
            continue
        sources = project.get("sources", [])
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            byte_count = source.get("byte_count", 0)
            if isinstance(byte_count, bool):
                continue
            if isinstance(byte_count, int):
                total += byte_count

    return total


def format_log_artifact_size(artifact: Mapping[str, Any]) -> str:
    byte_count = collect_log_artifact_byte_count(artifact)
    if byte_count >= 1024 * 1024:
        return f"{byte_count / 1024 / 1024:.1f} MB"
    return f"{byte_count / 1024:.1f} KB"
