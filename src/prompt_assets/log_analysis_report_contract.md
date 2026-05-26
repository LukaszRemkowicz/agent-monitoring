# Log Analysis Report Contract

- summary: Brief overview of the day's log health.
- severity: INFO|WARNING|CRITICAL.
- severity_rationale: One sentence explaining why severity is INFO, WARNING, or CRITICAL.
- key_findings: List of specific findings.
- evidence: Tool-backed facts used to produce the report.
- coverage_gaps: Sources or checks that were unavailable or inconclusive.
- recommendations: Concrete next steps; when evidence shows expected blocked scanner/probe noise, say no immediate routing, application, or mitigation-control change is indicated instead of inventing remediation.
- watch_only_items: Normal noise to keep observing without immediate action.
- trend_summary: Comparison against historical context when available; if no historical context was provided, say that no historical trend data was available.
