# Log Analysis Decision Skill

Use this skill before choosing `final_report`, `call_tools`, or `read_skills`.

## Decision Method

1. Apply the evidence-completeness gate from the critical decision rules.
2. Review current families in the declared attention order before using history.
3. Compare semantic meaning across project, source, severity, outcome, route,
   method, host, and message. Do not decide from counts alone.
4. Compare history only for equivalent scope. Treat a previous high-severity
   family as resolved only when complete current evidence covers that scope;
   otherwise use the smallest targeted verification.
5. Call only the smallest deterministic tool set that resolves material
   uncertainty about outcome, impact, scope, ownership, or confidence.

## Optional Skills

- Mandatory skills are already loaded. Request only optional skills listed in
  the prompt, and never request one marked `retrieved` again.
- Read `bot_detection` when unknown or material scanner/probe evidence needs
  interpretation. Skip it for known blocked watch-only 403/404 probes.
- Read `owasp_security` before `final_report` for possible security impact,
  successful sensitive-path access, auth/admin/API abuse, injection or path
  traversal, malicious-input 5xx, security-control failure, or unclear impact.
- An optional skill is not evidence until it has been retrieved.

## Report Requirement

Treat zero-line and unavailable sources as coverage gaps, not proof of health.
Use live tools for live-state claims. Synthesize impact, risk, confidence, and
trend; do not turn the report into a family inventory.
