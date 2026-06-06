# agent-monitoring

Standalone background monitoring app for the externalized log and sitemap
analysis workflow.

## Current Scope

The app currently provides the runtime foundation for monitoring workflows:

- service modules own business workflows for log and sitemap analysis
- repository/query modules own database reads and writes, keeping Tortoise
  calls out of command handlers
- Typer commands are thin orchestrators that parse options, open database
  lifecycle, call services, and format command output
- typed result objects describe service responses
- the shared `llm-core` provider boundary runs log-analysis and sitemap LLM
  requests through configured providers
- real workflow intelligence stays inside `src/agents.py`; services prepare
  application state and agents own MCP bootstrap, deterministic pre-LLM
  evidence, and LLM/tool loops
- tests cover service behavior, MCP contracts, and repository boundaries without
  calling live external services
- Docker Compose, CI/CD, pre-commit, production image builds, and release
  scripts support local and production operation

The app does not collect logs itself. MCP remains the source of truth for log
collection and artifact creation. The log-analysis command requests the MCP
workflow collection artifact, runs the LLM tool loop through deterministic MCP
follow-up tools, and persists the validated final report with summary, findings,
severity, recommendations, trend text, grouped-error fingerprints, LLM
token/cost metadata, and email state. Sitemap analysis fetches sitemap facts
deterministically, summarizes them with the configured LLM provider, persists
the report, and can send the monitoring email.

History-aware log analysis is documented in
`infra/docs/log_analysis_history_comparison.md`. That document explains how the
app gives the latest saved report and grouped-error fingerprint comparison to
the LLM, which Python guardrails still require deterministic MCP tool evidence,
and how reports must be worded when the LLM chooses a cheaper history-aware path.

Runtime settings are exposed through `src/conf.py`, following
the Django-style `settings` pattern. Tortoise models live in
`src/db/models.py`. Tortoise-specific config and database URL
construction live in `src/db/config.py`. Database lifecycle
helpers and the application lifespan context manager live in
`src/db/lifecycle.py`, matching the MCP project split between
config and startup/shutdown. Migration command aliases live in
`src/db/cli.py`.

LLM provider setup lives in `src/llm.py` and uses the shared `llm-core`
package. `configure_llm_providers()` reads global settings and registers the
configured model names directly as provider names, plus `mock` for tests and
local dry runs. `get_llm_provider(provider_name)` creates one registered
provider by name. Sitemap analysis uses `MONITORING_LLM_PROVIDER`; log analysis
always uses `MONITORING_LLM_STRONG_MODEL`. Configure it with:

- `MONITORING_LLM_PROVIDER`, defaulting to `gpt-4.1-mini`, used by sitemap
  analysis. Set it to a registered model name or `mock`.
- `MONITORING_LLM_FAST_MODEL`, defaulting to `gpt-4.1-mini`, registered as the
  cheaper OpenAI provider name
- `MONITORING_LLM_STRONG_MODEL`, defaulting to `gpt-5`, used by log analysis
  and registered as the stronger OpenAI provider name
- `MONITORING_PRIVATE_CONTEXT_PATH`, defaulting to
  `private/vps_monitoring_context.md`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`, optional
- `DEBUG=True` switches logs to colored, indented `pretty` JSON. `DEBUG` defaults to
  `False`; local Docker Compose sets it to `true`, while host-side `uv run`
  commands keep one-line JSON logs unless `DEBUG=True` is exported.

## Private VPS Context

MCP stays generic and manifest-driven. Detailed VPS architecture context belongs
to this monitoring app, because it can include private details about installed
services, domains, ports, security posture, and operational expectations.

Create this local file when you want the LLM report to understand your VPS:

```bash
mkdir -p private
$EDITOR private/vps_monitoring_context.md
```

The `private/` directory is ignored by Git, so this file is not published to
GitHub. This file is mandatory for log analysis. A safe example lives in
`infra/docs/vps_monitoring_context.example.md`.

Production Compose mounts the same private directory read-only into the app
container. By default deploy expects:

```text
private/vps_monitoring_context.md
```

Set `MONITORING_PRIVATE_CONTEXT_DIR` only when the private file lives outside the
repository checkout on the production host.

## Local Runtime

Use Docker Compose as the normal local runtime for monitoring jobs:

```bash
docker compose run --rm monitoring-app log_analysis
docker compose run --rm monitoring-app sitemap-analysis
docker compose run --rm monitoring-app check-mcp
```

### Log Analysis Flow

`log_analysis` starts in the Typer command, then delegates real workflow work to
`LogAnalysisService` and `MonitoringWorkflowAgent`. The initial LLM prompt is
built once per run; later LLM iterations append tool results, skill results, or
correction messages to the same conversation instead of rebuilding the original
prompt.

The high-level flow is:

```text
run_log_analysis()
  -> load workflow bundle
  -> read mandatory skills
  -> list projects
  -> collect_logs
  -> collect current grouped-error baseline
  -> optionally compare with previous grouped-error fingerprints
  -> _build_log_analysis_prompt(...)   # called once
  -> _run_tool_loop(...)
```

Before the first LLM request, Python calls deterministic MCP `group_errors` for
the collected projects/source keys. With `--compare-history`, it compares that
current grouped-error baseline with stored previous grouped-error fingerprints.
With `--no-compare-history`, it sends compact previous/current grouped-error
baselines instead of a Python diff. In both modes, current grouped-error
evidence is already available to the LLM; the LLM decides whether that is enough
or whether to call more deterministic tools.

Local Compose runs migrations inside the `monitoring-app` entrypoint before
starting monitoring commands, so migration errors are printed in the same command
output. The local app container uses host networking so `localhost` means the
local machine. That keeps the runtime close to the host-side commands:
`DATABASE_HOST=127.0.0.1`, `DATABASE_PORT=5438`, and
`LOG_ANALYSIS_MCP_URL=http://127.0.0.1:8001/mcp`. Local Compose also sets
`DEBUG=true` and `LOG_COLOR=always` for colored, indented JSON logs.

Override the MCP endpoint only when you want to call a different exposed HTTP
URL:

```bash
LOG_ANALYSIS_MCP_URL=https://mcp.example.com/mcp docker compose run --rm monitoring-app log_analysis
```

Host-side `uv run` commands are developer shortcuts, not the primary runtime for
DB-backed jobs. They can work, but the same runtime variables must be available
from `.env`, Doppler, or the shell:

```bash
DATABASE_HOST=127.0.0.1
DATABASE_PORT=5438
DATABASE_NAME=monitoring
DATABASE_USER=monitoring
DATABASE_PASSWORD=monitoring
LOG_ANALYSIS_MCP_URL=http://127.0.0.1:8001/mcp
MCP_WORKFLOW_JWT=...
DEBUG=true
```

Useful host-side commands:

```bash
uv run makemigrations add_monitoring_models
uv run migrate
uv run pytest
uv run ruff check src
uv run black --check src
uv run mypy --explicit-package-bases src
```

`uv run makemigrations` delegates to `aerich migrate --offline`, so it can
generate migration files from model metadata without a live database connection.
Pass the migration name as a positional argument; the wrapper stores generated
files using sequential names such as `001_initial_schema.py` and
`002_add_monitoring_models.py`.
On first use it initializes the Aerich migration folder with
`aerich init-migrations`, which is also offline.
This is the normal local command for creating migration files:

```bash
uv run makemigrations add_monitoring_models
```

## CI/CD

GitHub Actions workflows live in `.github/workflows` and mirror the MCP project
shape:

- `ci.yml` runs pre-commit quality checks, pytest through the shared reusable
  `python-tests-uv` workflow, CodeQL, version checks, and a Docker build smoke.
- `codeql.yml` runs the scheduled weekly CodeQL scan.
- `release.yml` tags `main` through the shared release workflow.

Operational scripts live under `infra/scripts`:

```bash
TAG=v1.2.3 infra/scripts/release/build.sh
TAG=v1.2.3 infra/scripts/release/deploy.sh
```

The release scripts build and deploy tagged images named
`prod-agent-monitoring:<TAG>`. Deployment applies migrations inside the
production Compose container and then runs the one-shot monitoring command,
defaulting to `log_analysis`.

Production Postgres data is stored outside Docker volumes at
`/var/lib/agent-monitoring/postgresql` by default. Override with
`POSTGRES_DATA_DIR` only when pointing to another durable host path.
