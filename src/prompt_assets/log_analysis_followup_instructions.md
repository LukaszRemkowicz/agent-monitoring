# Log Analysis Follow-Up Instructions

- Use these deterministic MCP tool results.
- Request more tools only if required.
- Return action=final_report when evidence is sufficient.
- If tool results show suspicious probing, auth abuse, credential scans, or exploit-like traffic, request read_skills with bot_detection or owasp_security before final_report unless that skill was already retrieved.
- Final reports must include severity_rationale.
- Explain INFO, WARNING, or CRITICAL using deterministic evidence, not tone.
- Do not claim a trend versus prior days unless historical context or tool results explicitly provide prior-run data.
- If no historical context is provided, trend_summary must say no historical trend data was available for comparison.
- Do not summarize high 4xx ratios on admin, API, or application paths as normal operation unless deterministic evidence or private monitoring context proves they are expected.
- Treat 4xx ratios at or above 20% as high enough to require explanation, and ratios at or above 50% as suspicious unless the paths are clearly scanner-only or expected noise.
- If high 4xx traffic is scanner-only or blocked-probe noise with no 5xx, no upstream errors, and no private-context expectation that the route is legitimate, put it in watch_only_items and avoid recommending routing, application, or mitigation-control changes.
- For repeated 405 POST / on an admin or application domain, treat it as likely bot/probe traffic unless private monitoring context defines POST / as a legitimate workflow or tool evidence shows user impact.
- Do not recommend fail2ban jail, ban-duration, or firewall changes when fail2ban is active and blocking the observed traffic unless evidence shows missed bans, inactive expected jails, jail errors, or repeated unbanned offenders.
- For zero-line sources, state that the source was not assessed from logs; do not claim it is healthy or error-free.
- Keep observed evidence, interpretation, coverage gaps, watch-only noise, and recommendations separate.
