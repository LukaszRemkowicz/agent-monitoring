from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from schemas import (
    LogAnalysisAttentionPriority,
    LogAnalysisGroupedErrorSignal,
    LogAnalysisPromptGroupedErrorFingerprintScope,
    LogAnalysisPromptGroupedErrorOmittedDetails,
)

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LONG_HEX = re.compile(r"^[0-9a-f]{12,}$", re.IGNORECASE)
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$", re.IGNORECASE)
_MESSAGE_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_MESSAGE_LONG_HEX = re.compile(
    r"\b(?=[0-9a-f]{12,}\b)(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b",
    re.IGNORECASE,
)
_MESSAGE_LONG_NUMBER = re.compile(r"\b\d{6,}\b")
_LABELED_NUMBER = re.compile(
    r"(?:error(?:[\s_-]*code)?|code|errno|sqlstate|status|exit[\s_-]*code)" r"\s*[:=#-]?\s*$",
    re.IGNORECASE,
)
_IP_ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_ISO_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?\b",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
_KNOWN_PROBE_ROUTE = re.compile(
    r"^/(?:"
    r"(?:[^/]+/)*\.env(?:[./-].*)?"
    r"|(?:[^/]+/)*\.git/config"
    r"|(?:[^/]+/)*\.aws/credentials"
    r"|wp-login\.php"
    r"|wp-admin(?:/.*)?"
    r"|wp-content(?:/.*)?"
    r"|xmlrpc\.php"
    r"|phpmyadmin(?:/.*)?"
    r"|setup\.php"
    r"|config\.php"
    r"|(?:[^/]+/)*(?:phpinfo|shell|eval-stdin)\.php"
    r")$",
    re.IGNORECASE,
)
PROMPT_GROUPED_ERROR_DETAIL_LIMIT_PER_ATTENTION = 8
PROMPT_GROUPED_ERROR_DETAIL_LIMITS: dict[LogAnalysisAttentionPriority, int | None] = {
    LogAnalysisAttentionPriority.ACTIONABLE: None,
    LogAnalysisAttentionPriority.INVESTIGATE: PROMPT_GROUPED_ERROR_DETAIL_LIMIT_PER_ATTENTION,
    LogAnalysisAttentionPriority.WATCH_ONLY: PROMPT_GROUPED_ERROR_DETAIL_LIMIT_PER_ATTENTION,
    LogAnalysisAttentionPriority.ROUTINE: PROMPT_GROUPED_ERROR_DETAIL_LIMIT_PER_ATTENTION,
}


class PromptGroupedErrorRow(Protocol):
    """Fields shared by detailed baseline and comparison prompt rows."""

    fingerprint: str
    project_name: str
    source_keys: list[str]
    attention_priority: LogAnalysisAttentionPriority


@dataclass(frozen=True)
class GroupedErrorSemantics:
    """Small agent-side family key derived from exact MCP facts."""

    signature: tuple[object, ...]
    attention_priority: LogAnalysisAttentionPriority
    normalized_paths: tuple[str, ...]
    request_methods: tuple[str, ...]
    request_hosts: tuple[str, ...]


@dataclass(frozen=True)
class CoalescedGroupedError:
    """One merged semantic family plus its exact-variant count."""

    signal: LogAnalysisGroupedErrorSignal
    semantics: GroupedErrorSemantics
    variant_count: int


def is_high_severity_group(signal: LogAnalysisGroupedErrorSignal) -> bool:
    """Return whether a group has high severity or a server-error status."""

    return signal.severity.casefold() in {"high", "critical"} or any(
        status_code >= 500 for status_code in signal.status_codes
    )


def build_grouped_error_semantics(
    signal: LogAnalysisGroupedErrorSignal,
) -> GroupedErrorSemantics:
    """Build a semantic family while leaving the exact MCP group unchanged."""

    request_paths = signal.request_paths or _path_from_fingerprint(signal.fingerprint)
    normalized_paths = tuple(sorted({_normalize_route(path) for path in request_paths if path}))
    request_methods = tuple(sorted({method.upper() for method in signal.request_methods}))
    request_hosts = tuple(sorted({host.casefold() for host in signal.request_hosts}))
    category = signal.category.casefold()
    status_codes = tuple(sorted(signal.status_codes))
    semantic_family = _normalize_message(signal.semantic_summary)
    message_family = ""
    if signal.has_explicit_message is True:
        message_family = _normalize_message(signal.message_summary)
    elif not semantic_family and (
        not normalized_paths or category in {"application_error", "runtime_error", "exception"}
    ):
        message_family = _normalize_message(signal.message_summary)
    exact_fallback = (
        _source_independent_fingerprint(signal.fingerprint)
        if not normalized_paths and not semantic_family and not message_family
        else ""
    )
    signature: tuple[object, ...] = (
        category,
        status_codes,
        request_methods,
        request_hosts,
        normalized_paths,
        signal.upstream_attempted,
        semantic_family,
        message_family,
        exact_fallback,
    )
    return GroupedErrorSemantics(
        signature=signature,
        attention_priority=_attention_priority(signal, normalized_paths=normalized_paths),
        normalized_paths=normalized_paths,
        request_methods=request_methods,
        request_hosts=request_hosts,
    )


def coalesce_grouped_errors(
    signals: Iterable[LogAnalysisGroupedErrorSignal],
) -> dict[tuple[object, ...], CoalescedGroupedError]:
    """Merge exact MCP variants that share one agent-side semantic identity."""

    merged: dict[tuple[object, ...], LogAnalysisGroupedErrorSignal] = {}
    variant_counts: dict[tuple[object, ...], dict[str, int]] = {}
    for signal in signals:
        semantics = build_grouped_error_semantics(signal)
        identity: tuple[object, ...] = (
            signal.project_name,
            tuple(sorted(signal.source_keys)),
            semantics.signature,
        )
        counts = variant_counts.setdefault(identity, {})
        exact_fingerprint = _canonical_exact_fingerprint(signal)
        counts[exact_fingerprint] = max(counts.get(exact_fingerprint, 0), signal.count)
        existing = merged.get(identity)
        if existing is None:
            merged[identity] = signal
            continue
        representative = min((existing, signal), key=lambda item: item.fingerprint)
        merged[identity] = representative.model_copy(
            update={
                "severity": _stronger_severity(existing.severity, signal.severity),
                "source_keys": sorted({*existing.source_keys, *signal.source_keys}),
                "request_paths": sorted({*existing.request_paths, *signal.request_paths}),
                "request_methods": sorted({*existing.request_methods, *signal.request_methods}),
                "request_hosts": sorted({*existing.request_hosts, *signal.request_hosts}),
                "status_codes": sorted({*existing.status_codes, *signal.status_codes}),
                "levels": sorted({*existing.levels, *signal.levels}),
            }
        )

    result: dict[tuple[object, ...], CoalescedGroupedError] = {}
    for identity, signal in merged.items():
        coalesced_signal = signal.model_copy(
            update={
                "count": sum(variant_counts[identity].values()),
                "variant_count": len(variant_counts[identity]),
            }
        )
        result[identity] = CoalescedGroupedError(
            signal=coalesced_signal,
            semantics=build_grouped_error_semantics(coalesced_signal),
            variant_count=len(variant_counts[identity]),
        )
    return result


def attention_priority_rank(priority: LogAnalysisAttentionPriority) -> int:
    """Return stable operational ordering, highest attention first."""

    return {
        LogAnalysisAttentionPriority.ACTIONABLE: 0,
        LogAnalysisAttentionPriority.INVESTIGATE: 1,
        LogAnalysisAttentionPriority.WATCH_ONLY: 2,
        LogAnalysisAttentionPriority.ROUTINE: 3,
    }[priority]


def project_grouped_error_prompt_rows[PromptGroupedErrorRowT: PromptGroupedErrorRow](
    rows: list[PromptGroupedErrorRowT],
    *,
    detail_limit_per_attention: int | None = None,
) -> tuple[list[PromptGroupedErrorRowT], LogAnalysisPromptGroupedErrorOmittedDetails | None]:
    """Bound low-priority rows while retaining every actionable row and family identity."""

    if detail_limit_per_attention is not None and detail_limit_per_attention < 0:
        raise ValueError("detail_limit_per_attention must be non-negative")

    detail_limits_by_attention: dict[LogAnalysisAttentionPriority, int | None] = (
        {priority: detail_limit_per_attention for priority in LogAnalysisAttentionPriority}
        if detail_limit_per_attention is not None
        else PROMPT_GROUPED_ERROR_DETAIL_LIMITS
    )

    selected: list[PromptGroupedErrorRowT] = []
    omitted: list[PromptGroupedErrorRowT] = []
    selected_counts: dict[LogAnalysisAttentionPriority, int] = {
        priority: 0 for priority in LogAnalysisAttentionPriority
    }
    for row in rows:
        priority = row.attention_priority
        detail_limit: int | None = detail_limits_by_attention[priority]
        if detail_limit is None or selected_counts[priority] < detail_limit:
            selected.append(row)
            selected_counts[priority] += 1
        else:
            omitted.append(row)

    if not omitted:
        return selected, None

    counts_by_attention: dict[str, int] = {}
    fingerprints_by_scope: dict[
        LogAnalysisAttentionPriority, dict[tuple[str, tuple[str, ...]], set[str]]
    ] = {}
    for row in omitted:
        priority = row.attention_priority
        priority_value = priority.value
        counts_by_attention[priority_value] = counts_by_attention.get(priority_value, 0) + 1
        scope = (row.project_name, tuple(sorted(row.source_keys)))
        fingerprints_by_scope.setdefault(priority, {}).setdefault(scope, set()).add(row.fingerprint)

    fingerprint_scopes_by_attention: dict[
        str, list[LogAnalysisPromptGroupedErrorFingerprintScope]
    ] = {}
    for priority in LogAnalysisAttentionPriority:
        scopes = fingerprints_by_scope.get(priority)
        if not scopes:
            continue
        fingerprint_scopes_by_attention[priority.value] = [
            LogAnalysisPromptGroupedErrorFingerprintScope(
                project_name=project_name,
                source_keys=list(source_keys),
                fingerprints=sorted(fingerprints),
            )
            for (project_name, source_keys), fingerprints in sorted(scopes.items())
        ]

    return selected, LogAnalysisPromptGroupedErrorOmittedDetails(
        count=len(omitted),
        detail_limit_per_attention=detail_limit_per_attention,
        detail_limits_by_attention={
            priority.value: limit for priority, limit in detail_limits_by_attention.items()
        },
        counts_by_attention=counts_by_attention,
        fingerprint_scopes_by_attention=fingerprint_scopes_by_attention,
    )


def _normalize_route(value: str) -> str:
    parsed = urlsplit(value)
    path = parsed.path or value.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        return path
    segments = [
        "{id}" if _is_dynamic_segment(segment) else segment
        for segment in path.split("/")
        if segment
    ]
    normalized = "/" + "/".join(segments)
    query_keys = sorted(
        {key.casefold() for key, _value in parse_qsl(parsed.query, keep_blank_values=True) if key}
    )
    if query_keys:
        normalized += "?" + "&".join(f"{{{key}}}" for key in query_keys)
    return normalized


def _is_dynamic_segment(value: str) -> bool:
    return bool(
        value.isdigit()
        or _UUID.fullmatch(value)
        or _LONG_HEX.fullmatch(value)
        or _ULID.fullmatch(value)
    )


def _normalize_message(value: str) -> str:
    normalized = _MESSAGE_UUID.sub("<id>", value)
    normalized = _MESSAGE_LONG_HEX.sub("<id>", normalized)
    normalized = _MESSAGE_LONG_NUMBER.sub(_normalize_number, normalized)
    normalized = _IP_ADDRESS.sub("<ip>", normalized)
    normalized = _ISO_TIMESTAMP.sub("<timestamp>", normalized)
    return _WHITESPACE.sub(" ", normalized.strip()).casefold()


def _normalize_number(match: re.Match[str]) -> str:
    prefix = match.string[max(0, match.start() - 48) : match.start()]
    return match.group(0) if _LABELED_NUMBER.search(prefix) else "<n>"


def _source_independent_fingerprint(value: str) -> str:
    _source, separator, identity = value.partition(":")
    return identity if separator else value


def _canonical_exact_fingerprint(signal: LogAnalysisGroupedErrorSignal) -> str:
    fingerprint = signal.fingerprint
    for source_key in sorted(signal.source_keys, key=len, reverse=True):
        prefix = f"{source_key}:"
        if fingerprint.startswith(prefix):
            return fingerprint[len(prefix) :]
    return fingerprint


def _stronger_severity(left: str, right: str) -> str:
    rank = {
        "critical": 4,
        "high": 3,
        "warning": 2,
        "medium": 2,
        "low": 1,
    }
    return max((left, right), key=lambda value: rank.get(value.casefold(), 0))


def _path_from_fingerprint(value: str) -> list[str]:
    path_index = value.find(":/")
    return [value[path_index + 1 :]] if path_index >= 0 else []


def _attention_priority(
    signal: LogAnalysisGroupedErrorSignal,
    *,
    normalized_paths: tuple[str, ...],
) -> LogAnalysisAttentionPriority:
    status_codes = set(signal.status_codes)
    known_probe = bool(normalized_paths) and all(
        _KNOWN_PROBE_ROUTE.fullmatch(path.split("?", 1)[0]) for path in normalized_paths
    )
    if known_probe and any(200 <= status_code < 300 for status_code in status_codes):
        return LogAnalysisAttentionPriority.ACTIONABLE
    if known_probe and any(300 <= status_code < 400 for status_code in status_codes):
        return LogAnalysisAttentionPriority.INVESTIGATE
    if (
        known_probe
        and signal.upstream_attempted is False
        and any(status_code >= 500 for status_code in status_codes)
    ):
        return LogAnalysisAttentionPriority.INVESTIGATE
    if is_high_severity_group(signal):
        return LogAnalysisAttentionPriority.ACTIONABLE
    if known_probe and status_codes and status_codes.issubset({403, 404}):
        return LogAnalysisAttentionPriority.WATCH_ONLY
    if (
        signal.category.casefold() in {"http_4xx", "application_error", "warning_signal"}
        or any(400 <= status_code < 500 for status_code in status_codes)
        or signal.severity.casefold() in {"medium", "warning"}
    ):
        return LogAnalysisAttentionPriority.INVESTIGATE
    return LogAnalysisAttentionPriority.ROUTINE
