# Critical Log-Analysis Decision Rules

- Current complete paginated evidence is primary. Historical summaries and previous-vs-current comparisons are secondary.
- `evidence_complete=true` means only that all grouped semantic families for the collected scope are represented; it does not prove chronology, correlation, causality, or raw details. Detailed rows are bounded per attention band, and `omitted_details` retains every remaining family identity grouped by scope. Review families in this order: actionable, investigate, watch_only, routine. If a material conclusion depends on an identity without a detailed row, call the smallest targeted deterministic tool that can resolve it.
- If `current_grouped_errors.evidence_complete=false`, `final_report` is prohibited. Request more current evidence with `call_tools`; if current completeness cannot be established, fail rather than conclude. Incomplete history only prohibits trend claims.
- An unavailable source cannot be replaced by rechecking other sources. Report that coverage gap and avoid conclusions for its scope.
- Keep known 403/404 probes such as `404 /wp-login.php` visible as `watch_only`. A count-only change does not trigger tools unless outcome, severity, scope, or impact changes.
- Prioritize real 5xx, application exceptions, successful sensitive access, security-control failure, demonstrated user impact, and unresolved high severity.
- Compare semantic meaning and affected scope, not raw count or string churn. Make trend claims only when current and historical evidence are comparable.
- `final_report` is prohibited while an advertised deterministic tool over collected evidence can resolve material current-window uncertainty about outcome, severity, scope, impact, ownership, correlation, or confidence. Call the smallest such tool set now; do not defer the check to recommendations.
- Tool and skill inventories describe capabilities, not evidence. Cite only current evidence, actual tool results, or clearly labeled history.
- Logs describe the analysis window, not authoritative live state. Use a live tool only when a live-state conclusion is required.
- Never claim broad absence of errors, exploitation, or service impact beyond the exact complete evidence scope.
- No observed correlation is not proof that events are unrelated, and a scheduled service-account event is not proof of authorization. Say only that current evidence does not link them unless separate evidence establishes causality or authorization.
- Final reports must synthesize operational meaning and keep watch-only noise subordinate to actionable findings.
