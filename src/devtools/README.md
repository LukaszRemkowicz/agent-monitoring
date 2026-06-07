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
`MONITORING_PRIVATE_CONTEXT_PATH` content included in the LLM prompt:

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
- `sensitive_path_success`: today includes a successful sensitive-path response
  that should be treated as critical

This lets us check whether the AI agent understands "what changed today versus
yesterday" instead of spending tokens rereading broad raw logs or hiding new
risk behind historical context.

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

Only scenario-specific evidence tools have rich static payloads. Other
production-advertised tools return an explicit generic fixture response if the
LLM asks for them. This keeps the advertised surface close to production while
keeping manual scenarios focused on the evidence needed for cost and
history-comparison checks.
