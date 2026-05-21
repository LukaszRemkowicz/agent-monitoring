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
- the shared `llm-core` provider boundary is wired without making live analysis
  requests yet
- real workflow intelligence stays inside `src/agents.py`; services prepare
  application state and agents own MCP bootstrap and future LLM/tool loops
- tests cover service behavior, MCP contracts, and repository boundaries without
  calling live external services
- Docker Compose, CI/CD, pre-commit, production image builds, and release
  scripts support local and production operation

The app does not collect logs itself. MCP remains the source of truth for log
collection and artifact creation. The log-analysis command now requests the MCP
workflow collection artifact and prepares the prompt/context that will be sent
to the LLM later. The app does not yet make a real LLM analysis request or send
email. The next monitoring workflow work should:

- call follow-up deterministic MCP tools to inspect the collected log artifact
- request sitemap artifacts when the sitemap flow moves beyond record creation
- pass collected artifacts into the monitoring agent for LLM analysis
- save summaries, findings, severity, recommendations, and token/cost metadata
- send report emails through a dedicated notification boundary

Runtime settings are exposed through `src/conf.py`, following
the Django-style `settings` pattern. Tortoise models live in
`src/db/models.py`. Tortoise-specific config and database URL
construction live in `src/db/config.py`. Database lifecycle
helpers and the application lifespan context manager live in
`src/db/lifecycle.py`, matching the MCP project split between
config and startup/shutdown. Migration command aliases live in
`src/db/cli.py`.

LLM provider setup lives in `src/llm.py` and uses the shared `llm-core`
package. Configure it with:

- `MONITORING_LLM_PROVIDER`, defaulting to `openai-fast`
- `MONITORING_LLM_FAST_MODEL`, defaulting to `gpt-4.1-mini`
- `MONITORING_LLM_STRONG_MODEL`, defaulting to `gpt-5`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`, optional
- `DEBUG=True` switches logs to colored, indented `pretty` JSON. `DEBUG` defaults to
  `False`; local Docker Compose sets it to `true`, while host-side `uv run`
  commands keep one-line JSON logs unless `DEBUG=True` is exported.

## Local Runtime

Use Docker Compose as the normal local runtime for monitoring jobs:

```bash
docker compose run --rm monitoring-app log_analysis
docker compose run --rm monitoring-app sitemap-analysis
docker compose run --rm monitoring-app check-mcp
```

Local Compose runs the `migrate` service before `monitoring-app`, so migrations
are applied through Compose without a wrapper script. The local app container
uses host networking so `localhost` means the local machine. That keeps the
runtime close to the host-side commands:
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
uv run pytest
uv run ruff check src
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
