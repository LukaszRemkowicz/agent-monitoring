from __future__ import annotations

from schemas import LogAnalysisGroupedErrorSignal


def is_high_severity_group(signal: LogAnalysisGroupedErrorSignal) -> bool:
    """Return whether a grouped-error signal should be treated as high severity."""

    if signal.severity.lower() in {"high", "critical"}:
        return True
    return any(status_code >= 500 for status_code in signal.status_codes)
