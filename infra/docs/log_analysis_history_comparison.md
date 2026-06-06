# History-Aware Log Analysis

History-aware log analysis reduces repeated daily work by giving the LLM the
latest saved report and a compact current-vs-previous comparison. The core rule
is:

```text
Python prepares facts and guardrails.
The LLM chooses whether current deterministic tools are needed.
```

The previous report is never proof of today's logs. Current log-content claims
still require current deterministic MCP tool results.

## Contents

- [Why This Exists](#why-this-exists)
- [CLI Switch](#cli-switch)
- [What Gets Compared](#what-gets-compared)
- [History Comparison Service](#history-comparison-service)
- [Comparison Prompt Guard](#comparison-prompt-guard)
- [What Python Does](#what-python-does)
- [What The LLM Decides](#what-the-llm-decides)
- [Full Flow](#full-flow)
- [Evidence Rules](#evidence-rules)
- [Code Reference](#code-reference)

## Why This Exists

Daily logs often contain repeated safe patterns:

- scanner traffic that returns 403 or 404
- expected forbidden image/API requests
- scheduler sources that emit no lines
- previous INFO reports with no service-impacting findings

Without history, the agent may spend a full tool loop every day to rediscover
the same conclusion. History-aware analysis gives the LLM current grouped-error
evidence plus previous context, so it can decide whether more tools are needed
or whether a cheaper short report is enough.

Cost reduction is a result of better agent judgment, not a Python shortcut.

## CLI Switch

The log-analysis command controls deterministic history comparison with one
Typer switch:

```bash
docker compose run --rm monitoring-app log_analysis --compare-history
docker compose run --rm monitoring-app log_analysis --no-compare-history
```

`--compare-history` enables the Python comparison service. When a previous
analysis exists, the agent sends the LLM a deterministic `grouped_error_diff`
instead of asking the LLM to compare previous and current grouped-error
baselines by itself.

In this mode, Python:

1. collects today's logs
2. runs current `group_errors`
3. loads previous grouped-error fingerprints from the saved report
4. compares exact fingerprints in Python
5. sends the compact diff to the LLM

`--no-compare-history` skips the Python diff. The agent still collects today's
logs and still runs current `group_errors`, but the prompt receives compact
previous/current grouped-error baselines instead of a computed
`grouped_error_diff`.

In both modes, current grouped-error evidence is available before the first LLM
decision. The difference is who compares the grouped-error baselines:

```text
--compare-history     Python compares exact fingerprints and sends grouped_error_diff.
--no-compare-history  LLM reviews compact previous/current baselines itself.
```

If no previous analysis exists, `--compare-history` cannot produce a real diff.
The prompt marks history comparison as unavailable and falls back to current
grouped-error evidence.

## What Gets Compared

There are two comparison layers.

### 1. Missing Source Guardrail

Python first checks whether each known source was missing before and whether it
is missing now:

```python
source_is_missing = source.zero_lines or source.status == "unavailable"
```

This detects simple observability changes such as:

```text
landingpage.traefik: had lines -> zero lines
landingpage.backend: unavailable -> collected
```

This is not full coverage analysis and it is not log-content analysis. It does
not compare line counts, timestamps, source paths, or raw log content. It only
answers: "did this source become missing, or stop being missing?"

The result is a guardrail. It tells the LLM that the current evidence may need
more checking when source observability changed.

### 2. Grouped-Error Fingerprints

After `collect_logs`, the agent asks MCP for current `group_errors` for the
projects and source keys collected in this run. When the previous report
contains stored `group_errors` fingerprints, Python compares those stored
fingerprints with the current grouped-error runs and builds a compact comparison
for the LLM:

```json
{
  "new_fingerprints": ["nginx:http_5xx:500:/api"],
  "resolved_fingerprints": ["nginx:http_4xx:404:/.env"],
  "persisting_fingerprints": ["nginx:http_4xx:404:/wp-login.php"],
  "worsened_fingerprints": ["nginx:http_4xx:404:/login"],
  "improved_fingerprints": [],
  "new_high_severity_fingerprints": ["nginx:http_5xx:500:/api"]
}
```

This is the real log-content comparison, but it still does not compare raw log
lines. It compares deterministic grouped-error summaries returned by MCP:

```json
{
  "fingerprint": "nginx:http_4xx:404:/.env",
  "project_name": "landingpage",
  "category": "http_4xx",
  "severity": "medium",
  "count": 5,
  "source_keys": ["nginx"],
  "request_paths": ["/.env"],
  "status_codes": [404],
  "message_summary": "404 on /.env scanner probe"
}
```

The comparison key is the exact `fingerprint` string. Python does not do
semantic matching between similar paths or messages.

For example:

```text
current fingerprints - previous fingerprints = new fingerprints
previous fingerprints - current fingerprints = resolved fingerprints
previous fingerprints intersect current fingerprints = persisting fingerprints
```

For persisting fingerprints, Python compares only the grouped count:

```text
current count > previous count = worsened
current count < previous count = improved
```

Python also marks a grouped-error as high severity when the grouped error says
`high` or `critical`, or when any grouped status code is `>= 500`.

Python still does not decide the report. It only says what changed and what
scope was checked.

### Scope Check For Resolved High-Severity Errors

If a high-severity fingerprint existed yesterday but is absent today, Python
checks whether today's current `group_errors` call actually covered the source
where yesterday's high-severity fingerprint lived.

Example:

```text
Yesterday: landingpage.backend had nginx:http_5xx:500:/api
Today: only landingpage.nginx was grouped
```

In that case the fingerprint is absent from the current grouped-error result,
but Python cannot confidently treat it as resolved, because the current run did
not inspect `landingpage.backend`.

## History Comparison Service

`LogAnalysisHistoryComparisonService` is the Python service that builds the
history comparison evidence. It is deliberately small in scope:

- it does not collect logs
- it does not call MCP
- it does not read the database
- it does not write the final report
- it does not decide whether the system is healthy

The agent gives the service already-prepared data:

```text
previous grouped-error runs from the saved LogAnalysis report
current grouped-error runs collected for today's log window
previous coverage snapshot
current coverage snapshot
previous report severity
```

The service returns deterministic comparison facts for the prompt.

### `compare_grouped_errors()`

This is the main grouped-error comparison entrypoint. It receives previous and
current grouped-error runs, delegates to `build_grouped_error_comparison()`, and
logs summary counters such as new, worsened, and high-severity fingerprint
counts.

### `build_grouped_error_comparison()`

This method performs the actual grouped-error comparison:

1. Flatten previous and current grouped-error runs into grouped-error rows.
2. Build dictionaries keyed by exact `fingerprint`.
3. Use set operations to calculate new, resolved, and persisting fingerprints.
4. Compare counts for persisting fingerprints to calculate worsened and improved
   fingerprints.
5. Detect new and resolved high-severity fingerprints.
6. Record the current tool scope by project/source.
7. Check whether resolved high-severity fingerprints were actually covered by
   today's current grouped-error scope.

The output is a `LogAnalysisGroupedErrorComparison`. It is complete enough for
Python and tests, but it can be too large for the LLM prompt.

### `compact_grouped_error_comparison_for_prompt()`

This method turns the full comparison into a smaller prompt payload:

- complete aggregate counts are preserved
- complete high-severity fingerprint lists are preserved
- changed examples are capped
- evidence-quality warnings are added when the cheap path is risky

This is the object the LLM usually sees as `grouped_error_diff`.

### `build_missing_source_comparison()`

This method compares only missing-source state between previous and current
coverage snapshots. A source is treated as missing when it was unavailable or
emitted zero lines.

It does not compare raw logs, line counts, timestamps, or source paths. Its job
is only to tell the LLM whether source observability changed enough to make the
history comparison less trustworthy.

### `find_unsupported_history_comparison_claims()`

This method runs after the LLM proposes a final report. It checks whether the
report makes broad current-health claims that are wider than the current
grouped-error evidence scope.

Example: if current grouped-error evidence only covered `landingpage.backend`,
the report should not say "all projects are healthy" or "no errors overall."
When this guard finds unsupported claims, the agent can ask the LLM to correct
the report.

## Comparison Prompt Guard

History comparison reduces cost, but it also creates one risk: the LLM may turn
limited comparison evidence into a broad current-health claim.

Example:

```text
Current grouped-error scope: landingpage.backend
Bad final report claim: "All projects are healthy and no upstream errors exist."
```

That claim is too broad. The comparison only proves something about the scoped
current `group_errors` evidence. It does not prove global health for every
project/source, and it does not prove facts from tools that were not run.

When `--compare-history` is enabled, the agent applies an extra final-report
guard before accepting the LLM response:

1. The LLM proposes `final_report`.
2. Python checks whether `history_comparison.status` is `available`.
3. Python reads `grouped_error_diff.current_tool_scope_by_project`.
4. Python scans the final report for broad current-run claims such as
   "all projects", "overall", "healthy", "no 5xx", "no upstream failures", or
   "no service impact".
5. If the claim is broader than the current grouped-error scope, Python rejects
   that final report and sends a correction prompt.

The correction prompt tells the LLM to return a new `final_report` with claims
scoped to `current_grouped_error_scope_by_project`.

This guard does not run in `--no-compare-history` mode because no Python
`grouped_error_diff` scope exists there. In no-compare mode, the prompt rules
still tell the LLM to stay within the provided previous/current grouped-error
baseline evidence.

## What Python Does

Python is only a guard and context builder.

It loads the previous successful report:

```python
previous_analysis = await self.repository.get_latest_before_date(analysis_date)
```

It collects today's logs from MCP:

```python
collect_logs = await self.mcp_client.collect_logs(
    since=log_window.since,
    until=log_window.until,
)
```

It collects a current grouped-error baseline from MCP before the first LLM
request:

```python
current_grouped_errors = await self._collect_current_grouped_errors(
    current_logs=collect_logs,
)
```

It sends compact previous history to the prompt:

```python
previous_analysis=PreviousLogAnalysisPromptContext(...)
```

It compares missing-source state as a guardrail:

```python
source_is_missing = source.zero_lines or source.status == "unavailable"
```

When `--compare-history` is enabled and a previous analysis exists, it compares
stored previous grouped-error fingerprints with the current grouped-error
baseline:

```python
grouped_error_comparison = (
    self.history_comparison_service.compare_grouped_errors(
        previous_grouped_errors=previous_analysis.fingerprints.grouped_error_runs,
        current_grouped_errors=current_grouped_errors,
    )
)
```

The history comparison service does not call MCP. It compares prepared
Pydantic objects that the agent already has.

Python does not decide that a final report is safe. It may require tools in hard
guard cases, but otherwise it exposes the comparison and lets the LLM choose.

## What The LLM Decides

When current grouped-error comparison exists and no hard guard requires tools,
Python sends:

```json
{
  "next_required_action": "choose_next_action",
  "final_report_allowed": true,
  "evidence_mode": "current_grouped_errors_available"
}
```

That means the LLM may choose either path:

```json
{"action": "call_tools", "tool_calls": [...]}
```

or:

```json
{"action": "final_report", "...": "..."}
```

If it skips additional tools, the report must stay limited to current grouped
errors, current collection metadata, and previous history. It must not claim that
other MCP tools were run.

If `grouped_error_comparison` is present, the LLM has current `group_errors`
evidence. It may cite that comparison directly, and it should call more tools
when the comparison shows new high-severity groups, 5xx groups, security-impacting
groups, or unclear impact.

## Full Flow

1. `LogAnalysisService.run_log_analysis()` loads the latest previous successful
   report before the requested analysis date.
2. `MonitoringWorkflowAgent.run_log_analysis()` loads the MCP workflow bundle,
   mandatory skills, projects, and today's `collect_logs` artifact.
3. `MonitoringWorkflowAgent._collect_current_grouped_errors()` runs current
   `group_errors` for the collected projects/source keys.
4. `_prepare_log_analysis_evidence()` chooses one prompt evidence shape:
   `history_comparison` when `--compare-history` has previous data, otherwise a
   compact grouped-error baseline.
5. `prepare_history_comparison_evidence_context()` runs the deterministic
   previous-vs-current grouped-error comparison and missing-source check when
   history comparison is enabled.
6. `_build_log_analysis_prompt()` builds structured prompt context.
7. `build_missing_source_comparison()` adds comparison information about
   zero-line or unavailable sources.
8. `build_grouped_error_comparison()` adds current-vs-previous grouped-error
   fingerprint changes when data exists.
9. The LLM receives previous history, current collection metadata, grouped-error
   comparison, available tools, and prompt rules.
10. The LLM chooses `call_tools`, `read_skills`, or `final_report`.
11. If tools run, follow-up prompts include current tool results and the same
   historical context.
12. The final report is stored with deterministic fingerprints and coverage
   snapshot for the next run.

## Evidence Rules

These statements require current MCP tool results:

```text
No HTTP 500 errors were detected today.
Scanner traffic was grouped as expected 404 noise.
Fail2ban has no service errors.
No upstream failures were found.
```

These statements are only supporting context. They are not enough by themselves
to finish a report:

```text
Previous analysis reported no HTTP 500 errors.
Current collection metadata does not show a missing-source state change.
```

These statements are allowed when `grouped_error_comparison` is present:

```text
Current grouped-error fingerprints match the previous run.
A new high-severity grouped-error fingerprint appeared.
The previous scanner fingerprint is no longer present in current grouped errors.
```

Hard guards still exist. Python requires tools when there is no current
grouped-error evidence. When grouped-error baseline or grouped-error comparison
evidence is already available, Python exposes missing-source state and previous
severity as context and lets the LLM decide whether more tools are needed.

Python requires tools when:

- no current grouped-error evidence is available yet
- missing-source state changed and grouped-error evidence is not enough to
  evaluate the changed scope
- previous severity was `WARNING` or `CRITICAL` and grouped-error evidence does
  not clearly resolve or explain the previous risk

For changed missing-source state, Python also gives scoped guidance:

```json
{
  "tool_scope_by_project": {
    "landingpage": ["traefik"]
  }
}
```

That scope tells the LLM where to start. It does not mean Python analyzed the
source content.

## Code Reference

| Area | Code |
| --- | --- |
| Load previous report | `LogAnalysisService.run_log_analysis()` |
| Collect current grouped errors | `MonitoringWorkflowAgent._collect_current_grouped_errors()` |
| Prepare prompt evidence | `MonitoringWorkflowAgent._prepare_log_analysis_evidence()` |
| Build prompt context | `MonitoringWorkflowAgent._build_log_analysis_prompt()` |
| Build missing-source guard signal | `LogAnalysisHistoryComparisonService.build_missing_source_comparison()` |
| Build history comparison context | `MonitoringWorkflowAgent.prepare_history_comparison_evidence_context()` |
| Compare grouped errors | `LogAnalysisHistoryComparisonService.build_grouped_error_comparison()` |
| Compact previous report | `MonitoringWorkflowAgent._compact_previous_analysis_for_prompt()` |
| Current source coverage facts | `MonitoringWorkflowAgent._build_current_coverage()` |
| Prompt schema | `LogAnalysisPromptContext` |
| Guard schema | `LogAnalysisSourceCoverageComparison` |
| Grouped-error comparison schema | `LogAnalysisGroupedErrorComparison` |
| Initial prompt rules | `src/prompt_assets/log_analysis_instructions.md` |
| Follow-up prompt rules | `src/prompt_assets/log_analysis_followup_instructions.md` |
| Stored baseline | `LogAnalysisFingerprintBuilder.build()` |
