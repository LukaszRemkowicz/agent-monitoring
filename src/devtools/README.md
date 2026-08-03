# Devtools

## Manual Log-Analysis Fixture

The manual fixture runner exists so we can manually evaluate the real
log-analysis agent with controlled MCP evidence.

The main use case is validating the cost-decrease work around deterministic
history comparison:

- seed a known previous-day analysis into a separate test database
- make fake MCP return controlled "today" log evidence
- run the real `MonitoringWorkflowAgent` with the real configured LLM provider
- check whether the agent uses compact history comparison before calling more
  tools
- compare token/cost output against older or `--no-compare-history` runs
- inspect whether the final report correctly explains what changed from the
  previous day

This is intentionally a manual tool, not a pytest scenario. The point is to see
how the real LLM agent behaves when deterministic code gives it a compact diff:
new grouped-error fingerprints, resolved fingerprints, source coverage, and
current scoped MCP evidence.

## Run It

```bash
docker compose --profile devtools run --rm manual-fixture --scenario backend_5xx --no-email
```

Use `--email` only when you explicitly want to verify the email path:

```bash
docker compose --profile devtools run --rm manual-fixture --scenario backend_5xx --email
```

Useful comparison run:

```bash
docker compose --profile devtools run --rm manual-fixture --scenario backend_5xx --no-email --no-compare-history
```

Manual fixture runs use a public-safe synthetic monitoring context by default.
Use `--private-context` only when you explicitly want the real local
`PROJECT_CONTEXT_PROMPT_PATH` content included in the LLM prompt:

```bash
docker compose --profile devtools run --rm manual-fixture --scenario backend_5xx --no-email --private-context
```

The command prints the final report plus LLM usage:

- `LLM tokens used`
- `LLM cost USD`
- `LLM report time`
- `Execution time`

Those fields are the manual signal for whether the history-comparison path is
actually reducing prompt/tool-loop cost.

## Why Fake MCP

The production MCP server returns live log artifacts, so it is hard to manually
replay the exact same evidence while tuning prompt guards and token usage.

`FakerMCP` is a fake MCP client that keeps the production agent path intact
while replacing only MCP responses. It lets us choose the evidence shape we want
the agent to reason about:

- `backend_5xx`: today introduces a new high-severity `/api/catalog` 502 family
  with related frontend SSR product-page timeout evidence
- `sensitive_path_success`: today returns non-empty HTTP 200 responses for
  `/.env`, `/.git/config`, and `/backup.sql`; the potential configuration,
  repository, and backup exposure must be treated as critical
- `watch_only_probes`: today repeats the same blocked 403/404 scanner families
  from yesterday; the agent should finish without optional skills or extra tools
- `ambiguous_security`: today introduces a clustered `/admin/login` 401 burst;
  the agent should use targeted security guidance and request proxy evidence
  only if grouped facts cannot resolve the impact
- `auth_burst_unrelated_export`: today contains an admin-login failure burst and
  a later privileged export; the agent must verify both authentication outcome
  and event correlation instead of treating same-day groups as one incident
- `coverage_gap`: today cannot collect `demo-shop.nginx`; the agent must report
  that gap and must not claim complete health or a resolved edge trend

Fixture project names, source names, archive paths, host labels, and route
examples are synthetic and intentionally public-safe. The scenarios preserve
the shape of useful monitoring evidence, not the real private topology. Generic
stack vocabulary such as nginx, Traefik, fail2ban, backend, frontend, worker,
scheduler, and common scanner paths remains when it helps the LLM reason about
the scenario.

This lets us check whether the AI agent understands "what changed today versus
yesterday" instead of spending tokens rereading broad raw logs or hiding new
risk behind historical context.

The five decision-focused scenarios intentionally share the same mocked
previous-day baseline:

| Scenario | History comparison | Efficient expected behavior |
| --- | --- | --- |
| `sensitive_path_success` | Three new high-severity HTTP 200 sensitive-path families appear | `CRITICAL`; preserve all three affected paths without claiming access logs prove response contents |
| `watch_only_probes` | Same blocked fingerprints persist | `INFO`; final report in the first LLM response; no optional skill or follow-up tool |
| `ambiguous_security` | A new admin authentication family appears | Inspect targeted proxy status evidence; read `owasp_security` only if interpretation remains unresolved; no broad log reread |
| `auth_burst_unrelated_export` | A login burst and privileged export appear on the same day without compact timing or ownership evidence | Gather login-outcome evidence plus targeted timing/ownership evidence; report that no current evidence links them without claiming compromise |
| `coverage_gap` | A previously collected source is now unavailable, making old edge fingerprints look resolved | Name `demo-shop.nginx` as a coverage gap; reject the false resolution signal and do not claim complete health or a stable trend |

These are manual expectations, not exact-output assertions. Wording and action
order may vary. The important checks are evidence use, unnecessary follow-ups,
severity reasoning, and total token cost. The runner prints optional skill reads
and LLM follow-up actions so those decisions are visible without querying the
database.

For the critical scenario, `group_errors` contains the first and last raw log
lines, while `grep_log_snapshot`, proxy inspection, and the incident bundle
return matching HTTP 200 evidence. The devtool does not create unused raw log
files; its mocked MCP responses are the replayable log contract.

`coverage_gap` is intentionally adversarial: the grouped-error diff can look
resolved while one source is unavailable. Passing means the agent treats
coverage as stronger evidence than the tempting resolved trend.

To stress autonomous evidence gathering without prescribing a tool sequence:

```bash
docker compose --profile devtools run --rm manual-fixture --scenario auth_burst_unrelated_export --no-email
```

Passing means the agent checks both whether any admin login succeeded and
whether current evidence links the export to the burst. Proxy inspection plus
either an incident bundle or targeted snapshot grep are both valid paths.

## History-Comparison Contract

The runner always exercises a fixed comparison shape:

- target analysis date: today in `LOG_TIMEZONE`
- previous comparison baseline: today minus 1 day
- older watch-only history: today minus 2 days

Arbitrary analysis dates are intentionally not supported by the manual fixture
command. The fake MCP scenarios are designed to answer one question: how does
the real agent interpret today's controlled fake MCP evidence against a seeded
previous-day baseline?

Fixture JSON files stay stable in Git, but `FakerMCP` rewrites embedded fixture
dates to the current run date before returning payloads. The agent therefore
sees date-consistent "today" evidence while the scenario files stay
deterministic.

## Seed Data

The devtool uses the `test-db` Postgres service from the `devtools` Compose
profile, not the normal monitoring database.

Before every manual fixture run, the command seeds deterministic initial data:

- clears any existing analysis row for today's target date
- upserts yesterday's baseline analysis
- upserts the day-before-yesterday watch-only analysis
- stores grouped-error fingerprints and coverage snapshots that the real
  history-comparison code can compare against the fake MCP "today" evidence

This seed step runs automatically inside `manual-fixture`; you do not need to
run it separately for normal manual checks.

The seed code lives in `devtools.data_seed` as plain helper functions. It is
imported by the manual fixture command and is not a separate Typer command.

## Production Shape

The fake workflow bootstrap mirrors the production MCP workflow inventory shape:

- 4 mandatory skills
- 2 optional skills
- 13 advertised tools

The fixture also stores local fixture versions of the workflow skill resources.
This keeps manual LLM judgment meaningful; placeholder skill text would test
routing but not whether retrieved guidance changes the report correctly.

Only scenario-specific evidence tools have rich static payloads. Other
production-advertised tools return an explicit generic fixture response if the
LLM asks for them. This keeps the advertised surface close to production while
keeping manual scenarios focused on the evidence needed for cost and
history-comparison checks.
