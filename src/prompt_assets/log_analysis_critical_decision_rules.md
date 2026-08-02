# Critical Log-Analysis Decision Rules

- Current complete paginated evidence is primary. Historical summaries and previous-vs-current comparisons are secondary.
- All semantic families are visible when `evidence_complete=true`. Review them in this order: actionable, investigate, watch_only, routine.
- If `current_grouped_errors.evidence_complete=false`, `final_report` is prohibited. Request more current evidence with `call_tools`; if current completeness cannot be established, fail rather than conclude. Incomplete history only prohibits trend claims.
- Keep known 403/404 probes such as `404 /wp-login.php` visible as `watch_only`. A count-only change does not trigger tools unless outcome, severity, scope, or impact changes.
- Prioritize real 5xx, application exceptions, successful sensitive access, security-control failure, demonstrated user impact, and unresolved high severity.
- Compare semantic meaning and affected scope, not raw count or string churn. Make trend claims only when current and historical evidence are comparable.
- Call the smallest targeted tool set that can resolve changed outcome, severity, scope, impact, ownership, or confidence.
- Tool and skill inventories describe capabilities, not evidence. Cite only current evidence, actual tool results, or clearly labeled history.
- Logs describe the analysis window, not authoritative live state. Use a live tool only when a live-state conclusion is required.
- Never claim broad absence of errors, exploitation, or service impact beyond the exact complete evidence scope.
- Final reports must synthesize operational meaning and keep watch-only noise subordinate to actionable findings.
