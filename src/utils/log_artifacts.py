from __future__ import annotations

import base64
import gzip
import json
from collections.abc import Mapping
from typing import Any

COMPRESSED_JSON_STORAGE = "gzip+base64-json"
COMPRESSED_JSON_VERSION = 1


def compress_json_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compressed JSON wrapper for a mapping stored in JSONField."""

    if is_compressed_json_mapping(payload):
        return dict(payload)

    raw_json = json.dumps(
        payload,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    compressed = gzip.compress(raw_json, mtime=0)
    return {
        "storage": COMPRESSED_JSON_STORAGE,
        "version": COMPRESSED_JSON_VERSION,
        "encoding": "gzip+base64",
        "original_json_bytes": len(raw_json),
        "compressed_bytes": len(compressed),
        "payload": base64.b64encode(compressed).decode("ascii"),
    }


def decompress_json_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the original mapping when a JSONField payload is compressed."""

    if not is_compressed_json_mapping(payload):
        return dict(payload)

    compressed_payload = payload.get("payload")
    if not isinstance(compressed_payload, str):
        return {}

    decompressed = gzip.decompress(base64.b64decode(compressed_payload))
    decoded = json.loads(decompressed.decode("utf-8"))
    if isinstance(decoded, dict):
        return decoded
    return {}


def is_compressed_json_mapping(payload: Mapping[str, Any]) -> bool:
    return payload.get("storage") == COMPRESSED_JSON_STORAGE


def collect_log_artifact_byte_count(artifact: Mapping[str, Any]) -> int:
    artifact = decompress_json_mapping(artifact)
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
