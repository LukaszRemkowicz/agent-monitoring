"""Deterministic previous-vs-current comparison for log-analysis prompts.

The module matches semantic error families and source coverage. It does not call
MCP, read the database, or decide the final report; the LLM interprets the
complete comparison evidence.
"""

from __future__ import annotations

from typing import Any

from exceptions import LogAnalysisComparisonMissingException
from logging_config import get_logger
from schemas import (
    LogAnalysisFinalReport,
    LogAnalysisGroupedErrorComparison,
    LogAnalysisGroupedErrorRunFingerprint,
    LogAnalysisGroupedErrorSignal,
    LogAnalysisPromptContext,
    LogAnalysisPromptGroupedErrorComparison,
    LogAnalysisPromptGroupedErrorExample,
    LogAnalysisSourceCoverageComparison,
    RecommendedAction,
)
from utils.grouped_errors import (
    attention_priority_rank,
    build_grouped_error_semantics,
    coalesce_grouped_errors,
    is_high_severity_group,
)
from utils.log_artifacts import build_missing_source_map
from utils.log_reports import build_final_report_search_text, split_report_sentences

logger = get_logger(__name__)

UNSUPPORTED_HISTORY_COMPARISON_CLAIM_TERMS: tuple[str, ...] = (
    "stable operation",
    "health remains stable",
    "healthy",
    "no new or worsening",
    "no 5xx",
    "no upstream",
    "upstream errors",
    "upstream failures",
    "no service impact",
    "without service impact",
    "no issues",
    "no errors",
    "no service-impacting",
    "successful exploitation",
    "service degradation",
    "application crashes",
)
UNSUPPORTED_HISTORY_COMPARISON_SCOPE_TERMS: tuple[str, ...] = (
    "any collected logs",
    "all collected logs",
    "all expected sources",
    "all sources",
    "all projects",
    "overall",
)


def _build_grouped_error_signals_by_identity(
    grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint],
) -> dict[tuple[object, ...], LogAnalysisGroupedErrorSignal]:
    families = coalesce_grouped_errors(
        signal for run in grouped_error_runs for signal in run.result.groups
    )
    return {identity: family.signal for identity, family in families.items()}


def _severity_rank(severity: str) -> int:
    return {
        "critical": 3,
        "high": 2,
        "warning": 1,
        "medium": 1,
        "low": 0,
    }.get(severity.casefold(), 0)


def _build_resolved_high_severity_scope(
    *,
    resolved_identities: set[tuple[object, ...]],
    previous_by_identity: dict[tuple[object, ...], LogAnalysisGroupedErrorSignal],
) -> dict[str, list[str]]:
    high_severity_groups = [
        previous_by_identity[identity]
        for identity in sorted(resolved_identities, key=repr)
        if is_high_severity_group(previous_by_identity[identity])
    ]
    return LogAnalysisHistoryComparisonService.build_grouped_error_signal_scope_by_project(
        high_severity_groups
    )


class LogAnalysisHistoryComparisonService:
    """Build deterministic previous-vs-current evidence for log analysis.

    The service compares already-prepared grouped-error fingerprints and source
    coverage snapshots. It is intentionally pure with respect to external
    systems: no MCP calls, no repository reads, and no LLM decisions happen
    here. Keeping this boundary sharp lets the agent pass small, factual
    comparison evidence to the LLM without hiding real current-log changes.
    """

    def compare_grouped_errors(
        self,
        *,
        previous_grouped_errors: list[LogAnalysisGroupedErrorRunFingerprint],
        current_grouped_errors: list[LogAnalysisGroupedErrorRunFingerprint],
    ) -> LogAnalysisGroupedErrorComparison | None:
        """Compare grouped-error runs and log comparison telemetry.

        `previous_grouped_errors` comes from the stored `LogAnalysisOut`
        fingerprint object. `current_grouped_errors` comes from the current
        run's deterministic `group_errors` MCP result after the agent has
        collected logs. This method only compares those two prepared artifacts;
        it does not fetch missing data or broaden scope by itself.
        """

        grouped_error_comparison: LogAnalysisGroupedErrorComparison | None = (
            self.build_grouped_error_comparison(
                previous_grouped_error_runs=previous_grouped_errors,
                current_grouped_error_runs=current_grouped_errors,
            )
        )
        logger.info(
            "completed log-analysis grouped-error comparison",
            extra={
                "event": "log_analysis_grouped_error_comparison_done",
                "previous_grouped_error_run_count": len(previous_grouped_errors),
                "current_grouped_error_run_count": len(current_grouped_errors),
                "grouped_error_comparison_available": grouped_error_comparison is not None,
                "current_group_count": (
                    grouped_error_comparison.current_group_count
                    if grouped_error_comparison is not None
                    else 0
                ),
                "new_fingerprint_count": (
                    len(grouped_error_comparison.new_fingerprints)
                    if grouped_error_comparison is not None
                    else 0
                ),
                "worsened_fingerprint_count": (
                    len(grouped_error_comparison.worsened_fingerprints)
                    if grouped_error_comparison is not None
                    else 0
                ),
                "new_high_severity_fingerprint_count": (
                    len(grouped_error_comparison.new_high_severity_fingerprints)
                    if grouped_error_comparison is not None
                    else 0
                ),
                "resolved_high_severity_fingerprint_count": (
                    len(grouped_error_comparison.resolved_high_severity_fingerprints)
                    if grouped_error_comparison is not None
                    else 0
                ),
            },
        )
        return grouped_error_comparison

    @staticmethod
    def build_grouped_error_comparison(
        *,
        previous_grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint],
        current_grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint],
    ) -> LogAnalysisGroupedErrorComparison | None:
        """Return the complete semantic-family diff between two baselines."""

        previous_groups: list[LogAnalysisGroupedErrorSignal] = [
            group for run in previous_grouped_error_runs for group in run.result.groups
        ]
        current_groups: list[LogAnalysisGroupedErrorSignal] = [
            group for run in current_grouped_error_runs for group in run.result.groups
        ]
        if not current_grouped_error_runs and not previous_groups and not current_groups:
            return None

        previous_by_identity = _build_grouped_error_signals_by_identity(previous_grouped_error_runs)
        current_by_identity = _build_grouped_error_signals_by_identity(current_grouped_error_runs)
        previous_identities = set(previous_by_identity)
        current_identities = set(current_by_identity)
        new_identities = current_identities - previous_identities
        resolved_identities = previous_identities - current_identities
        persisting_identities = previous_identities & current_identities
        new_fingerprints: list[str] = sorted(
            current_by_identity[identity].fingerprint for identity in new_identities
        )
        resolved_fingerprints: list[str] = sorted(
            previous_by_identity[identity].fingerprint for identity in resolved_identities
        )
        persisting_fingerprints: list[str] = sorted(
            current_by_identity[identity].fingerprint for identity in persisting_identities
        )
        worsened_identities: set[tuple[object, ...]] = set()
        improved_identities: set[tuple[object, ...]] = set()
        for identity in persisting_identities:
            previous = previous_by_identity[identity]
            current = current_by_identity[identity]
            previous_severity = _severity_rank(previous.severity)
            current_severity = _severity_rank(current.severity)
            if current_severity > previous_severity:
                worsened_identities.add(identity)
            elif current_severity < previous_severity:
                improved_identities.add(identity)
        worsened_fingerprints: list[str] = [
            current_by_identity[identity].fingerprint
            for identity in sorted(worsened_identities, key=repr)
        ]
        improved_fingerprints: list[str] = [
            current_by_identity[identity].fingerprint
            for identity in sorted(improved_identities, key=repr)
        ]
        new_high_severity_fingerprints: list[str] = [
            current_by_identity[identity].fingerprint
            for identity in sorted(new_identities, key=repr)
            if is_high_severity_group(current_by_identity[identity])
        ]
        resolved_high_severity_fingerprints: list[str] = [
            previous_by_identity[identity].fingerprint
            for identity in sorted(resolved_identities, key=repr)
            if is_high_severity_group(previous_by_identity[identity])
        ]
        current_tool_scope_by_project: dict[str, list[str]] = (
            LogAnalysisHistoryComparisonService.build_grouped_error_run_scope_by_project(
                current_grouped_error_runs
            )
        )
        resolved_high_severity_tool_scope_by_project: dict[str, list[str]] = (
            _build_resolved_high_severity_scope(
                resolved_identities=resolved_identities,
                previous_by_identity=previous_by_identity,
            )
        )
        resolved_high_severity_current_scope_covered: bool = (
            not resolved_high_severity_tool_scope_by_project
            or LogAnalysisHistoryComparisonService.tool_scope_covers(
                current_tool_scope_by_project,
                resolved_high_severity_tool_scope_by_project,
            )
        )
        current_changed_identities = new_identities | worsened_identities | improved_identities
        previous_changed_identities = (
            resolved_identities | worsened_identities | improved_identities
        )
        return LogAnalysisGroupedErrorComparison(
            available=True,
            current_tool_scope_by_project=current_tool_scope_by_project,
            previous_group_count=len(previous_by_identity),
            current_group_count=len(current_by_identity),
            new_fingerprints=new_fingerprints,
            resolved_fingerprints=resolved_fingerprints,
            persisting_fingerprints=persisting_fingerprints,
            worsened_fingerprints=worsened_fingerprints,
            improved_fingerprints=improved_fingerprints,
            new_high_severity_fingerprints=new_high_severity_fingerprints,
            resolved_high_severity_fingerprints=resolved_high_severity_fingerprints,
            resolved_high_severity_tool_scope_by_project=(
                resolved_high_severity_tool_scope_by_project
            ),
            resolved_high_severity_current_scope_covered=(
                resolved_high_severity_current_scope_covered
            ),
            current_changed_groups=[
                LogAnalysisHistoryComparisonService._compact_grouped_error_signal(
                    current_by_identity[identity]
                )
                for identity in sorted(current_changed_identities, key=repr)
            ],
            previous_changed_groups=[
                LogAnalysisHistoryComparisonService._compact_grouped_error_signal(
                    previous_by_identity[identity]
                )
                for identity in sorted(previous_changed_identities, key=repr)
            ],
            rationale=(
                "Current grouped-error families were compared with the previous "
                "deterministic baseline using the same conservative project, source, "
                "route, and message family identity as the current prompt."
            ),
        )

    @staticmethod
    def compact_grouped_error_comparison_for_prompt(
        comparison: LogAnalysisGroupedErrorComparison,
    ) -> LogAnalysisPromptGroupedErrorComparison:
        """Return every changed semantic family without raw seen-line payloads."""

        if comparison is None:
            raise LogAnalysisComparisonMissingException(
                "grouped-error comparison is required for prompt evidence"
            )

        evidence_quality_warnings: list[str] = (
            LogAnalysisHistoryComparisonService._build_grouped_error_evidence_quality_warnings(
                comparison
            )
        )
        current_changed_groups: list[LogAnalysisGroupedErrorSignal] = (
            LogAnalysisHistoryComparisonService._prioritize_current_changed_groups(comparison)
        )
        return LogAnalysisPromptGroupedErrorComparison(
            available=comparison.available,
            current_tool_scope_by_project=comparison.current_tool_scope_by_project,
            previous_group_count=comparison.previous_group_count,
            current_group_count=comparison.current_group_count,
            new_fingerprint_count=len(comparison.new_fingerprints),
            resolved_fingerprint_count=len(comparison.resolved_fingerprints),
            persisting_fingerprint_count=len(comparison.persisting_fingerprints),
            worsened_fingerprint_count=len(comparison.worsened_fingerprints),
            improved_fingerprint_count=len(comparison.improved_fingerprints),
            new_high_severity_fingerprint_count=len(comparison.new_high_severity_fingerprints),
            new_high_severity_fingerprints=comparison.new_high_severity_fingerprints,
            resolved_high_severity_fingerprint_count=len(
                comparison.resolved_high_severity_fingerprints
            ),
            resolved_high_severity_fingerprints=comparison.resolved_high_severity_fingerprints,
            resolved_high_severity_tool_scope_by_project=(
                comparison.resolved_high_severity_tool_scope_by_project
            ),
            resolved_high_severity_current_scope_covered=(
                comparison.resolved_high_severity_current_scope_covered
            ),
            evidence_quality_warnings=evidence_quality_warnings,
            current_changed_examples=[
                LogAnalysisHistoryComparisonService._compact_grouped_error_example(signal)
                for signal in current_changed_groups
            ],
            previous_changed_examples=[
                LogAnalysisHistoryComparisonService._compact_grouped_error_example(signal)
                for signal in comparison.previous_changed_groups
            ],
            rationale=(
                "Every changed semantic family is included. Seen-line payloads are omitted "
                "because current raw evidence remains available through deterministic tools."
            ),
        )

    @staticmethod
    def _prioritize_current_changed_groups(
        comparison: LogAnalysisGroupedErrorComparison,
    ) -> list[LogAnalysisGroupedErrorSignal]:
        return sorted(
            comparison.current_changed_groups,
            key=lambda group: (
                attention_priority_rank(build_grouped_error_semantics(group).attention_priority),
                0 if is_high_severity_group(group) else 1,
                group.fingerprint,
            ),
        )

    @staticmethod
    def _build_grouped_error_evidence_quality_warnings(
        comparison: LogAnalysisGroupedErrorComparison,
    ) -> list[str]:
        """Flag comparison shapes that should make the LLM cautious.

        These warnings do not decide severity. They tell the prompt that the
        cheap comparison path may be weak, for example when every current
        fingerprint is new, the previous baseline was empty, or high-severity
        families appeared or disappeared.
        """

        warnings: list[str] = []
        material_current_groups = [
            group
            for group in comparison.current_changed_groups
            if build_grouped_error_semantics(group).attention_priority.value
            in {"actionable", "investigate"}
        ]
        if (
            comparison.previous_group_count == 0
            and comparison.current_group_count > 0
            and material_current_groups
        ):
            warnings.append("previous_grouped_error_baseline_empty")
        if (
            0 < comparison.current_group_count == len(comparison.new_fingerprints)
            and material_current_groups
        ):
            warnings.append("all_current_grouped_error_fingerprints_are_new")
        worsened_fingerprints = set(comparison.worsened_fingerprints)
        material_worsened_group_present = any(
            group.fingerprint in worsened_fingerprints
            and build_grouped_error_semantics(group).attention_priority.value != "watch_only"
            for group in comparison.current_changed_groups
        )
        if material_worsened_group_present:
            warnings.append("worsened_grouped_error_fingerprints_present")
        if comparison.new_high_severity_fingerprints:
            warnings.append("new_high_severity_grouped_error_fingerprints_present")
        if comparison.resolved_high_severity_fingerprints:
            warnings.append("previous_high_severity_grouped_error_fingerprints_absent_from_current")
        return warnings

    @staticmethod
    def _compact_grouped_error_example(
        signal: LogAnalysisGroupedErrorSignal,
    ) -> LogAnalysisPromptGroupedErrorExample:
        """Trim a grouped-error signal down to fields useful as an example row."""

        semantics = build_grouped_error_semantics(signal)
        return LogAnalysisPromptGroupedErrorExample(
            fingerprint=signal.fingerprint,
            project_name=signal.project_name,
            category=signal.category,
            severity=signal.severity,
            count=signal.count,
            source_keys=signal.source_keys,
            request_paths=list(semantics.normalized_paths),
            status_codes=signal.status_codes,
            message_summary=signal.message_summary,
            upstream_attempted=signal.upstream_attempted,
            attention_priority=semantics.attention_priority,
            variant_count=signal.variant_count,
        )

    @staticmethod
    def _compact_grouped_error_signal(
        signal: LogAnalysisGroupedErrorSignal,
    ) -> LogAnalysisGroupedErrorSignal:
        """Keep only stable comparison facts for prompt-facing grouped-error deltas.

        Raw seen-line snippets and timestamps can be useful for incident bundles, but
        the comparison prompt usually needs identity, severity, count, source, path,
        status, and a short summary. Dropping the rest keeps history comparisons
        cheaper and avoids making the prompt look like raw log evidence.
        """

        return LogAnalysisGroupedErrorSignal(
            fingerprint=signal.fingerprint,
            project_name=signal.project_name,
            category=signal.category,
            severity=signal.severity,
            count=signal.count,
            source_keys=signal.source_keys,
            request_paths=signal.request_paths,
            request_methods=signal.request_methods,
            request_hosts=signal.request_hosts,
            status_codes=signal.status_codes,
            levels=signal.levels,
            message_summary=signal.message_summary,
            has_explicit_message=signal.has_explicit_message,
            upstream_attempted=signal.upstream_attempted,
            variant_count=signal.variant_count,
        )

    @staticmethod
    def build_grouped_error_run_scope_by_project(
        grouped_error_runs: list[LogAnalysisGroupedErrorRunFingerprint],
    ) -> dict[str, list[str]]:
        """Return the project/source scope represented by grouped-error runs.

        The scope comes from MCP arguments first because they describe what the
        tool was asked to inspect. If arguments are incomplete, the grouped
        result's searched source keys are used, and finally `*` means the run
        represents all sources known to that project in the grouped result.
        """

        source_keys_by_project: dict[str, set[str]] = {}
        for run in grouped_error_runs:
            project_name: str = str(
                run.arguments.get("project_name") or run.result.project_name or ""
            )
            if not project_name:
                continue
            raw_source_keys: object = run.arguments.get("source_keys")
            raw_source_key: object = run.arguments.get("source_key")
            if isinstance(raw_source_keys, list):
                source_keys: list[str] = [
                    str(source_key) for source_key in raw_source_keys if source_key
                ]
            elif raw_source_key:
                source_keys = [str(raw_source_key)]
            elif run.result.searched_source_keys:
                source_keys = run.result.searched_source_keys
            else:
                source_keys = ["*"]
            source_keys_by_project.setdefault(project_name, set()).update(source_keys)

        return {
            project_name: (["*"] if "*" in source_keys else sorted(source_keys))
            for project_name, source_keys in sorted(source_keys_by_project.items())
        }

    @staticmethod
    def build_grouped_error_signal_scope_by_project(
        signals: list[LogAnalysisGroupedErrorSignal],
    ) -> dict[str, list[str]]:
        """Return project/source scope represented by grouped-error signal rows.

        This is used mostly for resolved high-severity groups from the previous
        baseline. The current grouped-error scope must cover this source scope before
        the prompt can confidently say a high-severity family is absent today.
        """

        source_keys_by_project: dict[str, set[str]] = {}
        for signal in signals:
            if not signal.project_name:
                continue
            source_keys: list[str] = signal.source_keys or ["*"]
            source_keys_by_project.setdefault(signal.project_name, set()).update(source_keys)
        return {
            project_name: sorted(source_keys)
            for project_name, source_keys in sorted(source_keys_by_project.items())
        }

    @staticmethod
    def tool_scope_covers(
        current_scope_by_project: dict[str, list[str]],
        required_scope_by_project: dict[str, list[str]],
    ) -> bool:
        """Return whether current grouped-error scope covers a required scope.

        `*` in the current scope means all sources for that project were covered.
        `*` in the required scope means only another `*` can prove coverage. This
        conservative rule prevents the LLM from treating a resolved fingerprint as
        verified absent when the current MCP call did not inspect the old source.
        """

        for project_name, required_source_keys in required_scope_by_project.items():
            current_source_keys: list[str] | None = current_scope_by_project.get(project_name)
            if not current_source_keys:
                return False
            current_source_set: set[str] = set(current_source_keys)
            if "*" in current_source_set:
                continue
            required_source_set: set[str] = set(required_source_keys)
            if "*" in required_source_set:
                return "*" in current_source_set
            if not required_source_set.issubset(current_source_set):
                return False
        return True

    @staticmethod
    def build_tool_scope_by_project(changed_sources: list[str]) -> dict[str, list[str]]:
        """Convert `project.source` names into scoped MCP tool guidance."""

        tool_scope_by_project: dict[str, list[str]] = {}
        for source_name in changed_sources:
            project_name, separator, source_key = source_name.partition(".")
            if not separator or not project_name or not source_key:
                continue
            tool_scope_by_project.setdefault(project_name, []).append(source_key)
        return {
            project_name: sorted(source_keys)
            for project_name, source_keys in sorted(tool_scope_by_project.items())
        }

    @staticmethod
    def build_missing_source_comparison(
        previous_coverage_snapshot: dict[str, Any],
        current_coverage_snapshot: dict[str, Any],
        previous_severity: str,
    ) -> LogAnalysisSourceCoverageComparison:
        """Compare missing-source state between previous and current runs.

        This is intentionally narrower than a full coverage diff. A source is
        treated as missing when it was unavailable or emitted zero lines; changes
        in line counts, timestamps, paths, or source inventory are not compared
        here. Missing-source changes affect trust in the cheap grouped-error
        baseline path, so the result can recommend scoped tool calls. The agent
        may later relax that recommendation when current grouped-error evidence
        already covers the relevant scope.
        """

        previous_has_missing_logs_by_source: dict[str, bool] = build_missing_source_map(
            previous_coverage_snapshot
        )
        current_has_missing_logs_by_source: dict[str, bool] = build_missing_source_map(
            current_coverage_snapshot
        )

        changed_sources: list[str] = [
            source_name
            for source_name in sorted(
                set(previous_has_missing_logs_by_source) & set(current_has_missing_logs_by_source)
            )
            if previous_has_missing_logs_by_source[source_name]
            != current_has_missing_logs_by_source[source_name]
        ]
        tool_scope_by_project: dict[str, list[str]] = (
            LogAnalysisHistoryComparisonService.build_tool_scope_by_project(changed_sources)
        )
        if previous_severity in {"WARNING", "CRITICAL"}:
            return LogAnalysisSourceCoverageComparison(
                available=True,
                source_coverage_changed=bool(changed_sources),
                changed_sources=changed_sources,
                tool_scope_by_project=tool_scope_by_project,
                recommended_action=RecommendedAction.CALL_TOOLS,
                rationale=(
                    f"Previous analysis severity was {previous_severity}; "
                    "current deterministic evidence is required before final_report "
                    "to verify whether the prior warning or critical condition is "
                    "still present."
                ),
            )
        if changed_sources:
            return LogAnalysisSourceCoverageComparison(
                available=True,
                source_coverage_changed=True,
                changed_sources=changed_sources,
                tool_scope_by_project=tool_scope_by_project,
                recommended_action=RecommendedAction.CALL_TOOLS,
                rationale=(
                    "Previous and current source coverage state differ; call "
                    "deterministic tools scoped to changed_sources before final_report."
                ),
            )
        return LogAnalysisSourceCoverageComparison(
            available=True,
            source_coverage_changed=False,
            changed_sources=[],
            tool_scope_by_project={},
            recommended_action=RecommendedAction.LLM_MAY_DECIDE,
            rationale=(
                "Previous and current source coverage state metadata match. Let the LLM "
                "decide whether current deterministic tools are needed before final_report."
            ),
        )

    @staticmethod
    def find_unsupported_history_comparison_claims(
        *,
        final_report: LogAnalysisFinalReport,
        prompt_context: LogAnalysisPromptContext,
    ) -> list[str]:
        """Return broad current-run claims unsupported by history comparison evidence.

        Grouped-error history comparison can be enough for a cheap report, but
        its evidence is only as broad as the current `group_errors` calls. If
        current tools covered only part of the collected sources, the final
        report cannot claim current-run health for all projects or unscoped
        projects. Historical text may still be cited as historical context.
        """

        prompt_compacted_evidence: Any = prompt_context.evidence.get("prompt_compacted")
        comparison_payload: Any = (
            prompt_compacted_evidence.get("grouped_error_diff")
            if isinstance(prompt_compacted_evidence, dict)
            else None
        )
        comparison: LogAnalysisPromptGroupedErrorComparison | None = (
            LogAnalysisPromptGroupedErrorComparison.model_validate(comparison_payload)
            if comparison_payload is not None
            else None
        )
        if comparison is None or not comparison.current_tool_scope_by_project:
            return []

        collection_scope_by_project: dict[str, set[str]] = {
            project.project_name: {source.source_key for source in project.sources}
            for project in prompt_context.collection.projects
            if project.project_name
        }
        scoped_projects: set[str] = set(comparison.current_tool_scope_by_project)
        collected_projects: set[str] = set(collection_scope_by_project)
        if collection_scope_by_project:
            scope_is_limited: bool = any(
                project_name not in scoped_projects
                or (
                    "*" not in comparison.current_tool_scope_by_project[project_name]
                    and not source_keys.issubset(
                        set(comparison.current_tool_scope_by_project[project_name])
                    )
                )
                for project_name, source_keys in collection_scope_by_project.items()
            )
        else:
            scope_is_limited = any(
                "*" not in source_keys
                for source_keys in comparison.current_tool_scope_by_project.values()
            )
        if not scope_is_limited:
            return []

        report_text: str = build_final_report_search_text(final_report)
        report_sentences: list[str] = split_report_sentences(report_text)
        unscoped_projects: set[str] = collected_projects - scoped_projects
        violations: list[str] = []
        for sentence in report_sentences:
            if not sentence:
                continue
            if "previous" in sentence or "historical" in sentence:
                continue
            has_broad_claim: bool = any(
                term in sentence for term in UNSUPPORTED_HISTORY_COMPARISON_CLAIM_TERMS
            )
            has_broad_scope: bool = any(
                term in sentence for term in UNSUPPORTED_HISTORY_COMPARISON_SCOPE_TERMS
            )
            if has_broad_claim and has_broad_scope:
                violations.append(
                    "Final report makes a broad current-run health claim outside the "
                    "current grouped-error evidence scope."
                )
            for project_name in sorted(unscoped_projects):
                if project_name.lower() in sentence and has_broad_claim:
                    violations.append(
                        f"Final report makes a current-run health claim for unscoped "
                        f"project '{project_name}'."
                    )

        return sorted(set(violations))
