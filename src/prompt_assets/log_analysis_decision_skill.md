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
6. Error groups prove only the listed responses. For actionable authentication
   or security findings, retrieve targeted status evidence before `final_report`
   when it can determine success and change severity. Never infer success or its
   absence from error groups.
7. An unavailable source cannot be replaced by rechecking other sources. Report
   the gap, avoid trend claims for that scope, and call another tool only for a
   separate material question.

## Optional Skills

- Mandatory skills are already loaded. Request only optional skills listed in
  the prompt, and never request one marked `retrieved` again.
- Read `bot_detection` when unknown or material scanner/probe evidence needs
  interpretation. Skip it for known blocked watch-only 403/404 probes.
- After gathering needed deterministic facts, read `owasp_security` only when it
  can still change unclear security interpretation or incident framing. Skip it
  when mandatory guidance already establishes the outcome, severity, and action.
- An optional skill is not evidence until it has been retrieved.

## Report Requirement

Treat zero-line and unavailable sources as coverage gaps, not proof of health.
Use live tools for live-state claims. Synthesize impact, risk, confidence, and
trend; do not turn the report into a family inventory.
Access logs prove path, status, and response byte count, not response contents.
Call sensitive-file disclosure potential unless separate evidence confirms it.
Without that evidence, do not say the file was exposed, disclosed, compromised,
or publicly retrievable, and do not claim exfiltration.
