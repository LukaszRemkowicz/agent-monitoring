"""Deterministic history comparison helpers for log-analysis prompts.

This module owns the Python-side comparison work that should not be delegated
to the LLM: matching grouped-error fingerprints, detecting high-risk deltas,
checking source coverage changes, and compacting those facts into bounded prompt
payloads. It deliberately does not call MCP tools, read the database, or decide
the final report. The agent/service layer supplies already-collected current
grouped errors and the previous persisted analysis; the LLM then interprets the
compact evidence and decides whether more deterministic MCP tools are needed.
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
from utils.grouped_errors import is_high_severity_group
from utils.log_artifacts import build_missing_source_map
from utils.log_reports import build_final_report_search_text, split_report_sentences

logger = get_logger(__name__)

UNSUPPORTED_HISTORY_COMPARISON_CLAIM_TERMS: tuple[str, ...] = (
    "stable operation",
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
        """Return the full deterministic fingerprint diff between two baselines.

        Fingerprints are treated as the stable identity for a grouped-error
        family. The result separates new, resolved, persisting, worsened, and
        improved families, plus high-severity subsets that need explicit prompt
        treatment. Counts and changed examples are complete at this layer; later
        prompt compaction may cap example rows but not the aggregate counts.
        """

        previous_groups: list[LogAnalysisGroupedErrorSignal] = [
            group for run in previous_grouped_error_runs for group in run.result.groups
        ]
        current_groups: list[LogAnalysisGroupedErrorSignal] = [
            group for run in current_grouped_error_runs for group in run.result.groups
        ]
        if not current_grouped_error_runs and not previous_groups and not current_groups:
            return None

        previous_by_fingerprint: dict[str, LogAnalysisGroupedErrorSignal] = {
            signal.fingerprint: signal for signal in previous_groups
        }
        current_by_fingerprint: dict[str, LogAnalysisGroupedErrorSignal] = {
            signal.fingerprint: signal for signal in current_groups
        }
        previous_fingerprints: set[str] = set(previous_by_fingerprint)
        current_fingerprints: set[str] = set(current_by_fingerprint)
        new_fingerprints: list[str] = sorted(current_fingerprints - previous_fingerprints)
        resolved_fingerprints: list[str] = sorted(previous_fingerprints - current_fingerprints)
        persisting_fingerprints: list[str] = sorted(previous_fingerprints & current_fingerprints)
        worsened_fingerprints: list[str] = [
            fingerprint
            for fingerprint in persisting_fingerprints
            if current_by_fingerprint[fingerprint].count
            > previous_by_fingerprint[fingerprint].count
        ]
        improved_fingerprints: list[str] = [
            fingerprint
            for fingerprint in persisting_fingerprints
            if current_by_fingerprint[fingerprint].count
            < previous_by_fingerprint[fingerprint].count
        ]
        new_high_severity_fingerprints: list[str] = [
            fingerprint
            for fingerprint in new_fingerprints
            if is_high_severity_group(current_by_fingerprint[fingerprint])
        ]
        resolved_high_severity_fingerprints: list[str] = [
            fingerprint
            for fingerprint in resolved_fingerprints
            if is_high_severity_group(previous_by_fingerprint[fingerprint])
        ]
        resolved_high_severity_groups: list[LogAnalysisGroupedErrorSignal] = [
            previous_by_fingerprint[fingerprint]
            for fingerprint in resolved_high_severity_fingerprints
        ]
        current_tool_scope_by_project: dict[str, list[str]] = (
            LogAnalysisHistoryComparisonService.build_grouped_error_run_scope_by_project(
                current_grouped_error_runs
            )
        )
        resolved_high_severity_tool_scope_by_project: dict[str, list[str]] = (
            LogAnalysisHistoryComparisonService.build_grouped_error_signal_scope_by_project(
                resolved_high_severity_groups
            )
        )
        resolved_high_severity_current_scope_covered: bool = (
            not resolved_high_severity_tool_scope_by_project
            or LogAnalysisHistoryComparisonService.tool_scope_covers(
                current_tool_scope_by_project,
                resolved_high_severity_tool_scope_by_project,
            )
        )
        current_changed_fingerprints: set[str] = (
            set(new_fingerprints)
            | set(worsened_fingerprints)
            | set(improved_fingerprints)
            | set(new_high_severity_fingerprints)
        )
        previous_changed_fingerprints: set[str] = (
            set(resolved_fingerprints)
            | set(worsened_fingerprints)
            | set(improved_fingerprints)
            | set(resolved_high_severity_fingerprints)
        )
        return LogAnalysisGroupedErrorComparison(
            available=True,
            current_tool_scope_by_project=current_tool_scope_by_project,
            previous_group_count=len(previous_groups),
            current_group_count=len(current_groups),
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
                    current_by_fingerprint[fingerprint]
                )
                for fingerprint in sorted(current_changed_fingerprints)
                if fingerprint in current_by_fingerprint
            ],
            previous_changed_groups=[
                LogAnalysisHistoryComparisonService._compact_grouped_error_signal(
                    previous_by_fingerprint[fingerprint]
                )
                for fingerprint in sorted(previous_changed_fingerprints)
                if fingerprint in previous_by_fingerprint
            ],
            rationale=(
                "Current grouped-error fingerprints were collected and compared with "
                "previous deterministic grouped-error fingerprints. The LLM decides "
                "whether this comparison is enough or whether more tools are needed."
            ),
        )

    @staticmethod
    def compact_grouped_error_comparison_for_prompt(
        comparison: LogAnalysisGroupedErrorComparison,
        *,
        max_examples: int = 8,
    ) -> LogAnalysisPromptGroupedErrorComparison:
        """Return bounded grouped-error comparison evidence for the LLM prompt.

        The prompt payload keeps exact aggregate counts and complete
        high-severity fingerprint lists, while capping verbose changed examples.
        This keeps token cost predictable without losing the facts that drive
        safety decisions. Passing `None` is a caller bug because this method
        compacts an existing comparison; no-comparison cases should be handled
        before calling it.
        """

        if comparison is None:
            raise LogAnalysisComparisonMissingException(
                "grouped-error comparison is required for prompt compaction"
            )

        evidence_quality_warnings: list[str] = (
            LogAnalysisHistoryComparisonService._build_grouped_error_evidence_quality_warnings(
                comparison
            )
        )
        next_evidence_hint: str = (
            "call_tools_for_broader_current_evidence_before_final_report"
            if evidence_quality_warnings
            else "history_comparison_may_be_enough_if_examples_show_low_risk_continuity"
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
            resolved_high_severity_fingerprints=(comparison.resolved_high_severity_fingerprints),
            resolved_high_severity_tool_scope_by_project=(
                comparison.resolved_high_severity_tool_scope_by_project
            ),
            resolved_high_severity_current_scope_covered=(
                comparison.resolved_high_severity_current_scope_covered
            ),
            evidence_quality_warnings=evidence_quality_warnings,
            next_evidence_hint=next_evidence_hint,
            current_changed_examples=[
                LogAnalysisHistoryComparisonService._compact_grouped_error_example(signal)
                for signal in comparison.current_changed_groups[:max_examples]
            ],
            previous_changed_examples=[
                LogAnalysisHistoryComparisonService._compact_grouped_error_example(signal)
                for signal in comparison.previous_changed_groups[:max_examples]
            ],
            rationale=(
                "Grouped-error comparison is compacted for the prompt: counts are complete, "
                "high-severity new fingerprints are complete, and changed groups are capped "
                "to representative examples. Call tools for exact full fingerprint lists."
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
        if comparison.previous_group_count == 0 and comparison.current_group_count > 0:
            warnings.append("previous_grouped_error_baseline_empty")
        if 0 < comparison.current_group_count == len(comparison.new_fingerprints):
            warnings.append("all_current_grouped_error_fingerprints_are_new")
        if comparison.previous_group_count > 0 and (
            comparison.current_group_count >= comparison.previous_group_count * 3
        ):
            warnings.append("current_group_count_far_above_previous_group_count")
        if comparison.worsened_fingerprints:
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

        return LogAnalysisPromptGroupedErrorExample(
            fingerprint=signal.fingerprint,
            project_name=signal.project_name,
            category=signal.category,
            severity=signal.severity,
            count=signal.count,
            source_keys=signal.source_keys,
            request_paths=signal.request_paths[:3],
            status_codes=signal.status_codes,
            message_summary=signal.message_summary,
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
            status_codes=signal.status_codes,
            levels=signal.levels,
            message_summary=signal.message_summary,
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
            project_name: sorted(source_keys)
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

        scoped_projects: set[str] = set(comparison.current_tool_scope_by_project)
        collected_projects: set[str] = {
            project.project_name
            for project in prompt_context.collection.projects
            if project.project_name
        }
        scope_is_limited: bool = bool(collected_projects - scoped_projects) or any(
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
