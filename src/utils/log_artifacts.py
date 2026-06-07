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


def build_missing_source_map(coverage_snapshot: Mapping[str, Any]) -> dict[str, bool]:
    """Map `project.source` names to whether that source is missing."""

    has_missing_logs_by_source: dict[str, bool] = {}
    for project in coverage_projects(coverage_snapshot):
        project_name: str = str(project.get("project_name") or "")
        for source in coverage_sources(project):
            source_key: str = str(source.get("source_key") or "")
            if not project_name or not source_key:
                continue
            source_name: str = f"{project_name}.{source_key}"
            has_missing_logs_by_source[source_name] = source_is_missing(source)
    return has_missing_logs_by_source


def coverage_projects(coverage_snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return project mappings from a persisted coverage snapshot."""

    projects: object = coverage_snapshot.get("projects")
    if not isinstance(projects, list):
        return []
    return [project for project in projects if isinstance(project, Mapping)]


def coverage_sources(project: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return source mappings from one coverage project entry."""

    sources: object = project.get("sources")
    if not isinstance(sources, list):
        return []
    return [source for source in sources if isinstance(source, Mapping)]


def source_is_missing(source: Mapping[str, Any]) -> bool:
    """Return whether a collected source was unavailable or emitted zero lines."""

    return bool(source.get("zero_lines")) or str(source.get("status") or "") == "unavailable"
