# Log Analysis Report Contract

- summary: Brief current-window impact, risk, confidence, and whether action is needed. Do not inventory families.
- severity: INFO|WARNING|CRITICAL.
- severity_rationale: One sentence tied to current deterministic evidence.
- key_findings: Operational conclusions answering "so what?" Use one representative example only when material.
- evidence: Two to five concise evidence-to-conclusion bullets. Do not repeat raw rows.
- grouped-error baseline review: State stable, materially changed, or uncertain. Mention only material family shifts and follow-up evidence.
- grouped-error baseline resolved sensitive access: Explicitly mention when previous successful sensitive-path access is absent from complete current evidence.
- resolved high-severity history: Explicitly mention disappeared high-severity families. Call tools only when current scope is incomplete or impact remains unclear.
- coverage_gaps: Unavailable or inconclusive sources and checks.
- recommendations: Concrete actions. Do not invent remediation for confirmed watch-only scanner noise.
- watch_only_items: Normal noise worth observing without immediate action.
- trend_summary: Compare only equivalent current and historical scope; otherwise state that no comparable trend is available.
