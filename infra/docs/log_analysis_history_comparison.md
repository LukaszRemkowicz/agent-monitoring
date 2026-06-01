# History-Aware Log Analysis

History-aware log analysis reduces repeated daily LLM work by using the previous
successful report as context. It does not replace deterministic MCP tools, and
it must not treat yesterday's report as proof of today's logs.

## Contents

- [Why This Feature Exists](#why-this-feature-exists)
- [What It Solves](#what-it-solves)
- [Why It Is Implemented This Way](#why-it-is-implemented-this-way)
- [Full Flow Of Analysis With Historical Context](#full-flow-of-analysis-with-historical-context)
- [Code And Data Reference](#code-and-data-reference)

## Why This Feature Exists

The daily log-analysis workflow often sees the same safe patterns:

- scanner traffic returning 404 or 403
- forbidden image/API requests that are expected
- scheduler sources with no emitted lines
- no upstream errors
- no HTTP 500 responses
- no fail2ban service errors

Without historical context, the agent may spend a full LLM/tool loop every day
to rediscover the same answer.

The goal is to make stable days cheaper:

```text
If yesterday was safe and today's collection shape is unchanged,
the report may use history and current metadata instead of repeating
a full deterministic analysis.
```

But the goal is not to blindly trust history:

```text
Previous analysis is context.
Current MCP tool results are evidence.
```

That distinction is the whole feature.

## What It Solves

The feature solves three practical problems.

### 1. Repeated Cost For Stable Logs

If yesterday's report already classified repeated scanner noise as watch-only,
and today's sources collected in the same way, a full broad analysis is usually
not useful.

The cheaper path lets the LLM write a comparison report from:

```python
previous_analysis
source_missing_logs_comparison
current_coverage
```

without calling every deterministic tool again.

### 2. Safe Verification After Real Problems

If the previous report was `WARNING` or `CRITICAL`, the system must not trust the
old report. It must call deterministic MCP tools again.

Example:

```text
Yesterday: backend had HTTP 500 errors
Today: missing-log metadata looks unchanged
```

Even if missing-log state matches, current tools are still required because the question
is not "did the same sources collect?" The question is:

```text
Are the HTTP 500 errors still present today?
```

Coverage metadata cannot answer that.

### 3. Scoped Checks When Coverage Changes

If yesterday was `INFO` but one source changes coverage, the agent should inspect
that source first instead of broadening into a full VPS-wide scan.

Example:

```text
Yesterday:
  landingpage.traefik had log lines
  landingpage.backend had log lines

Today:
  landingpage.traefik has zero lines
  landingpage.backend still has log lines
```

The changed source is:

```text
landingpage.traefik
```

The important behavior is simple: the monitor should start with the source that
changed, not with every available log source. The internal representation for
that scope is explained later in the full flow.

## Why It Is Implemented This Way

The implementation keeps a hard boundary between deterministic code and LLM
interpretation.

### Python Compares Metadata

Python compares stable facts that do not require interpretation:

```python
source_name = f"{project_name}.{source_key}"
has_missing_logs = source.zero_lines or source.status == "unavailable"
```

This can detect missing-log state changes:

```text
landingpage.traefik: had lines -> zero lines
landingpage.backend: unavailable -> collected
```

It cannot decide whether the log content is safe.

Python must not infer:

- whether 404s are scanner noise
- whether 403s are expected access restrictions
- whether HTTP 500s are present
- whether fail2ban is active
- whether a suspicious path is an attack
- whether there was service impact

Those are current evidence and interpretation questions.

### MCP Tools Gather Current Evidence

Current log-content claims require deterministic MCP tool results.

For example, this wording requires current tool evidence:

```text
No HTTP 500 errors were detected today.
Scanner traffic was grouped as expected 404 noise.
Fail2ban has no service errors.
```

Without current tools, the report must use softer history wording:

```text
Previous analysis reported no HTTP 500 errors, and current collection metadata
does not show a source missing-log state change.
```

### The LLM Interprets Evidence Under Prompt Rules

The LLM receives structured controls:

```python
LogAnalysisPromptContext(
    previous_analysis=...,
    source_missing_logs_comparison=...,
    current_coverage=...,
    evidence_mode=...,
    next_required_action=...,
    final_report_allowed=...,
)
```

The important fields are:

| Field | Purpose |
| --- | --- |
| `previous_analysis` | Compact previous successful report. |
| `source_missing_logs_comparison` | Deterministic decision from Python. |
| `current_coverage` | Current zero-line and unavailable-source facts. |
| `evidence_mode` | Tells the LLM what kind of evidence exists. |
| `next_required_action` | Tells the LLM whether it must call tools or may report. |
| `final_report_allowed` | Prevents final report when tools are required. |

The prompt rules live in:

```text
src/prompt_assets/log_analysis_instructions.md
src/prompt_assets/log_analysis_followup_instructions.md
```

They enforce the central rule:

```text
Previous analysis is context, not current evidence.
```

## Full Flow Of Analysis With Historical Context

This is the complete path from command execution to stored history for the next
run.

At a high level, the workflow does this:

1. Find the last successful report before the current analysis date.
2. Collect today's logs from MCP.
3. Compare yesterday's stored coverage with today's collected coverage.
4. Decide whether the LLM may write a comparison report or must call tools.
5. If tools are needed, keep them scoped when only one source changed.
6. Write the final report.
7. Store a compact baseline for the next run.

The rest of this section maps that user-visible behavior to the actual code.

### Step 1: Service Loads Previous History

The flow starts in:

```python
LogAnalysisService.run_log_analysis()
```

The service asks the repository for the latest previous report:

```python
previous_analysis = await self.repository.get_latest_before_date(analysis_date)
```

Then it passes that report to the agent:

```python
agent_context = await self.agent.run_log_analysis(
    analysis_date=analysis_date,
    log_window=log_window,
    historical_context=historical_context,
    previous_analysis=previous_analysis,
)
```

### Step 2: Agent Collects Current Logs

The agent loads the workflow and collects today's log snapshot:

```python
workflow = await self.mcp_client.get_workflow_bundle()
mandatory_skills = await self._read_mandatory_skills(workflow.mandatory_skills)
available_projects = await self.mcp_client.list_projects()
collect_logs = await self.mcp_client.collect_logs(
    since=log_window.since,
    until=log_window.until,
)
```

At this point the agent has:

```text
previous_analysis  -> database history
collect_logs       -> current collection metadata and snapshot references
workflow           -> MCP tools, skills, and prompt contract
```

### Step 3: Agent Builds The History Decision

The decision is built in:

```python
MonitoringWorkflowAgent._build_log_analysis_prompt()
```

This method does more than create prompt text. It builds the control object that
decides what the LLM is allowed to do first.

Inside the method, the order is:

1. convert the previous database report into typed comparison data
2. compare previous missing-log state with current missing-log state
3. compact the previous report before sending it to the LLM
4. choose the evidence mode
5. choose the next required LLM action
6. build `LogAnalysisPromptContext`

First, it converts the database row into typed context:

```python
previous_analysis_context = PreviousLogAnalysisContext.from_analysis(previous_analysis)
```

Then it compares previous and current missing-log state:

```python
source_missing_logs_comparison = self._build_source_missing_logs_comparison(
    previous_analysis=previous_analysis_context,
    collect_logs=collect_logs,
)
```

The core of `_build_source_missing_logs_comparison()` is simple: for every known source, it
checks whether that source had missing logs yesterday and whether it has missing
logs today.

It first builds a map from the previous report:

```python
previous_has_missing_logs_by_source = {}

for previous_project in previous_analysis.coverage_snapshot.projects:
    for previous_source in previous_project.sources:
        source_name = (
            f"{previous_project.project_name}.{previous_source.source_key}"
        )
        previous_has_missing_logs_by_source[source_name] = (
            previous_source.zero_lines
            or previous_source.status == "unavailable"
        )
```

Then it builds the same map from the current `collect_logs` artifact:

```python
current_has_missing_logs_by_source = {}

for project in collect_logs.projects:
    for source in project.sources:
        source_name = f"{project.project_name}.{source.source_key}"
        current_has_missing_logs_by_source[source_name] = (
            source.line_count == 0
            or source.status == "unavailable"
        )
```

The boolean value means:

```text
False -> this source had log lines and was available
True  -> this source had zero lines or was unavailable
```

Then it compares only sources that exist in both maps:

```python
changed_sources = [
    source_name
    for source_name in sorted(
        set(previous_has_missing_logs_by_source)
        & set(current_has_missing_logs_by_source)
    )
    if previous_has_missing_logs_by_source[source_name]
    != current_has_missing_logs_by_source[source_name]
]
```

So a source is considered changed when its missing-log state changed:

```text
False -> True   source had logs yesterday, but is missing today
True  -> False  source was missing yesterday, but has logs today
```

The source name uses `project.source` format because that is convenient for
comparison:

Example:

```python
changed_sources = ["landingpage.traefik"]
```

MCP tools do not accept that flat string directly. They expect a project name
and source keys, so `_build_tool_scope_by_project()` converts it:

```python
tool_scope_by_project = {
    "landingpage": ["traefik"],
}
```

That scoped shape is passed to the LLM in `source_missing_logs_comparison`, so the first
tool call can inspect only the changed source unless current evidence justifies
a broader check.

After the source comparison, previous severity decides whether tools are required.

If the previous report was risky, tools are always required:

```python
if previous_analysis.severity in {"WARNING", "CRITICAL"}:
    return LogAnalysisSourceMissingLogsComparison(
        recommended_action="call_tools",
        missing_logs_changed=bool(changed_sources),
        changed_sources=changed_sources,
        tool_scope_by_project=tool_scope_by_project,
        rationale="Previous warning or critical condition must be verified.",
    )
```

If previous severity was not risky but missing-log state changed, scoped tools are
required:

```python
if changed_sources:
    return LogAnalysisSourceMissingLogsComparison(
        recommended_action="call_tools",
        missing_logs_changed=True,
        changed_sources=changed_sources,
        tool_scope_by_project=tool_scope_by_project,
        rationale="Missing-log state changed; inspect changed sources before report.",
    )
```

Only when previous severity is safe and missing-log state did not change can the first
LLM action be a final report:

```python
return LogAnalysisSourceMissingLogsComparison(
    recommended_action="final_report",
    missing_logs_changed=False,
    changed_sources=[],
    tool_scope_by_project={},
    rationale="Previous and current source missing-log state metadata match.",
)
```

That gives `_build_source_missing_logs_comparison()` three practical outcomes:

| Situation | Result |
| --- | --- |
| No previous report | Tools required. |
| Previous `WARNING` or `CRITICAL` | Tools required. |
| Previous `INFO` and missing-log state changed | Scoped tools required. |
| Previous `INFO` and missing-log state matches | Final report allowed from history and metadata. |

The returned object looks like:

```python
LogAnalysisSourceMissingLogsComparison(
    available=True,
    missing_logs_changed=False,
    changed_sources=[],
    tool_scope_by_project={},
    recommended_action="final_report",
    rationale="Previous and current source missing-log state metadata match.",
)
```

Or, when one source changed:

```python
LogAnalysisSourceMissingLogsComparison(
    available=True,
    missing_logs_changed=True,
    changed_sources=["landingpage.traefik"],
    tool_scope_by_project={"landingpage": ["traefik"]},
    recommended_action="call_tools",
    rationale="Previous and current source missing-log state differ...",
)
```

After that, `_build_log_analysis_prompt()` removes source-level previous
coverage before sending history to the LLM:

```python
prompt_previous_analysis_context = (
    self._compact_previous_analysis_for_prompt(previous_analysis_context)
    if previous_analysis_context is not None
    else None
)
```

This is why the method has both:

```text
previous_analysis_context        -> full typed previous report for Python
prompt_previous_analysis_context -> compact previous report for the LLM
```

Python needs source-level coverage to compare yesterday and today. The LLM does
not need every old source row, because old source rows are not current evidence.

Then the method decides whether history requires tools:

```python
source_missing_logs_requires_tools = (
    source_missing_logs_comparison is not None
    and source_missing_logs_comparison.recommended_action == "call_tools"
)
```

That boolean drives the prompt controls.

### Step 4: Prompt Controls The First LLM Action

The history decision becomes prompt controls:

```python
if source_missing_logs_requires_tools:
    evidence_mode = "source_missing_logs_changed_requires_tools"
elif previous_analysis_context is not None:
    evidence_mode = "metadata_and_previous_analysis_only"
else:
    evidence_mode = "mcp_tool_results_required"
```

Meaning:

| Evidence mode | When it is used |
| --- | --- |
| `mcp_tool_results_required` | There is no previous report, so current tools are required. |
| `metadata_and_previous_analysis_only` | Previous report exists and missing-log state did not require tools. |
| `source_missing_logs_changed_requires_tools` | Previous report exists, but severity or missing-log state means tools are required. |

Then it sets the first required action:

```python
if previous_analysis_context is not None and not source_missing_logs_requires_tools:
    next_required_action = "final_report"
    final_report_allowed = True
else:
    next_required_action = "call_tools"
    final_report_allowed = False
```

Finally, those values are placed into `LogAnalysisPromptContext`:

```python
LogAnalysisPromptContext(
    previous_analysis=prompt_previous_analysis_context,
    source_missing_logs_comparison=source_missing_logs_comparison,
    current_coverage=self._build_current_coverage(collect_logs),
    evidence_mode=evidence_mode,
    next_required_action=next_required_action,
    final_report_allowed=(
        previous_analysis_context is not None and not source_missing_logs_requires_tools
    ),
    collection=collect_logs,
)
```

This is what prevents the LLM from doing a full analysis when history is enough,
and also prevents it from skipping tools when current evidence is required.

### Step 5: LLM Either Reports Or Calls Tools

If the previous `INFO` report and current missing-log state match, the first LLM action
may be:

```json
{
  "action": "final_report",
  "summary": "Current collection metadata is consistent with the previous safe analysis..."
}
```

If missing-log state changed, the first action should be scoped:

```json
{
  "action": "call_tools",
  "tool_calls": [
    {
      "tool_name": "inspect_proxy_activity",
      "arguments": {
        "project_name": "landingpage",
        "source_keys": ["traefik"]
      }
    }
  ]
}
```

If the previous report was `WARNING` or `CRITICAL`, the first action must call
tools to verify whether the prior problem is still present.

### Step 6: Successful Report Stores The Next Baseline

After the final report succeeds, the service stores compact history for the next
run:

```python
fingerprint_packet = LogAnalysisFingerprintBuilder.build(
    collect_logs=agent_context.collect_logs,
    tool_results=agent_context.tool_results,
    final_report=agent_context.final_report,
    log_window_since=agent_context.log_window_since,
    log_window_until=agent_context.log_window_until,
)
```

Then it persists:

```python
deterministic_fingerprint=fingerprint_packet.deterministic_fingerprint
evidence_fingerprints=fingerprint_packet.evidence_fingerprints
known_patterns=fingerprint_packet.known_patterns
coverage_snapshot=fingerprint_packet.coverage_snapshot
fingerprint_version=fingerprint_packet.fingerprint_version
```

That saved data becomes `previous_analysis` for a later run.

## Code And Data Reference

Main implementation files:

| File | Responsibility |
| --- | --- |
| `src/services/log_analyse.py` | Loads previous history, calls the agent, persists final report and next baseline. |
| `src/agents.py` | Builds history-aware prompt context and runs the LLM/MCP tool loop. |
| `src/services/log_fingerprints.py` | Builds compact fingerprints and coverage snapshots. |
| `src/schemas.py` | Defines typed history, prompt, coverage, and final report contracts. |
| `src/prompt_assets/log_analysis_instructions.md` | First-call prompt rules for history and evidence. |
| `src/prompt_assets/log_analysis_followup_instructions.md` | Follow-up prompt rules after tool calls. |

Important methods:

| Method | Purpose |
| --- | --- |
| `LogAnalysisService.run_log_analysis()` | Starts the workflow, loads history, and stores the next baseline. |
| `LogAnalysisRepository.get_latest_before_date()` | Finds the previous report used as historical context. |
| `MonitoringWorkflowAgent.run_log_analysis()` | Collects MCP data and runs the analysis loop. |
| `MonitoringWorkflowAgent._build_log_analysis_prompt()` | Builds the structured context sent to the LLM. |
| `MonitoringWorkflowAgent._build_source_missing_logs_comparison()` | Decides whether history is enough, scoped tools are required, or full tools are required. |
| `MonitoringWorkflowAgent._build_tool_scope_by_project()` | Converts changed sources into MCP tool scope. |
| `MonitoringWorkflowAgent._compact_previous_analysis_for_prompt()` | Removes source-level old coverage from the prompt while preserving summary and totals. |
| `LogAnalysisFingerprintBuilder.build()` | Builds the compact saved history after a successful report. |
| `LogAnalysisFingerprintBuilder.build_coverage_snapshot()` | Builds source missing-log state metadata from the current collection. |

Important data contracts:

| Schema | Purpose |
| --- | --- |
| `PreviousLogAnalysisContext` | Typed previous database report used by Python comparison. |
| `PreviousLogAnalysisPromptContext` | Compact history sent to the LLM. |
| `LogAnalysisSourceMissingLogsComparison` | Deterministic result of comparing current missing-log state to previous missing-log state. |
| `LogAnalysisCurrentCoverage` | Current zero-line and unavailable-source facts. |
| `LogAnalysisPromptContext` | Full structured prompt context for the LLM action loop. |
