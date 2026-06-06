from __future__ import annotations

import re

from schemas import LogAnalysisFinalReport


def build_final_report_search_text(final_report: LogAnalysisFinalReport) -> str:
    """Flatten final-report text fields for lightweight unsupported-claim checks."""

    parts: list[str] = [
        final_report.summary,
        final_report.severity_rationale,
        final_report.recommendations,
        final_report.trend_summary,
        *final_report.key_findings,
        *final_report.evidence,
        *final_report.coverage_gaps,
        *final_report.watch_only_items,
    ]
    return "\n".join(part.lower() for part in parts if part)


def split_report_sentences(text: str) -> list[str]:
    """Split report prose into coarse sentences for guardrail matching."""

    return [
        sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+|[\n;]+", text) if sentence.strip()
    ]
