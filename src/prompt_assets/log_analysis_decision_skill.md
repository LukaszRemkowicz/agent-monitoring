# Log Analysis Decision Skill

Use this skill before choosing `final_report`, `call_tools`, or `read_skills`.

## Decision Goal

Minimize follow-up tools when `grouped_error_diff` proves the current and previous
grouped-error baselines are operationally equivalent.

Do not investigate count-only changes when project, source, severity, category,
status class, route/message family, and source coverage are unchanged.

Do investigate with targeted tools when the diff shows new, worsened, resolved
high-severity, changed source coverage, changed affected scope, or an evidence
quality warning.

## Grouped-Error Baseline Method

When `evidence.kind=grouped_error_baseline` and both `evidence.previous_grouped_errors` and `evidence.current_grouped_errors` are available:

1. Normalize examples into semantic families before comparing them. Use project, source ownership, category, severity, status class, route intent, message meaning, and coverage context. Do not compare only counts.
2. Decide whether the two baselines prove the same operational story. Stable means the same affected projects, source ownership, categories, severity posture, status classes, route/message families, and coverage confidence.
3. Treat a change as material when it changes or obscures likely impact, cause, scope, severity, coverage confidence, route intent, source ownership, or whether the pattern is benign.
4. Use `final_report` only when the grouped-error evidence is complete enough to prove a stable low-risk baseline. If examples are omitted or the visible examples/distributions do not prove stability, do not assume the hidden fingerprints are harmless.
5. Use `call_tools` when current fingerprints introduce, remove, or shift source ownership, route intent, message meaning, severity posture, status class, project scope, or coverage confidence. Choose the smallest set of tools that can resolve the uncertainty, but make that pass complete enough for the affected semantic family.
6. Use `read_skills` only when a listed optional skill would change the interpretation of the observed facts. Do not read optional skills just to restate known low-risk scanner noise.

## Report Requirement

If you return `final_report` from grouped_error_baseline evidence, write analyst synthesis rather than a grouped-error inventory. Say why the two baselines prove a stable, materially changed, or uncertain operational story. Name only material family shifts that affect impact, risk, confidence, or follow-up.
