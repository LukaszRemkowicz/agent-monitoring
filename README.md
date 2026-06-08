# agent-monitoring

Standalone monitoring app for production log analysis and sitemap checks.

It runs as one-shot Docker Compose commands. Cron scheduling lives in the
separate `devops` repository.

## What It Does

- runs daily log analysis through the MCP log workflow
- runs weekly sitemap analysis
- stores monitoring reports in Postgres
- sends short email reports
- sends failure emails for command failures that happen inside the job flow
- keeps private VPS context out of Git

The app does not collect raw logs by itself. MCP collects log artifacts and
returns references that this app stores with the report.

## Production Quickstart

Run these commands from the production `agent-monitoring` checkout.

### 1. Prepare Project Context Prompt

Create the project context prompt file on the VPS:

```bash
mkdir -p private
$EDITOR private/vps_monitoring_context.md
```

This file is required for `log_analysis`. It should describe the VPS services,
domains, ports, and operational expectations that the report needs to understand.

The `private/` directory is ignored by Git.

### 2. Configure Secrets

Production commands need these values from Doppler, `.env`, or exported shell
environment:

```bash
DATABASE_NAME=agent_monitoring
DATABASE_USER=agent_monitoring
DATABASE_PASSWORD=...
MCP_URL=https://...
MCP_WORKFLOW_JWT=...
OPENAI_API_KEY=...
EMAIL_HOST=...
EMAIL_USERNAME=...
EMAIL_PASSWORD=...
EMAIL_FROM=...
EMAIL_TO=...
SITE_DOMAIN=example.com
```

Optional values:

```bash
LLM_DEFAULT_MODEL=gpt-4.1-mini
LLM_FAST_MODEL=gpt-4.1-mini
LLM_STRONG_MODEL=gpt-5
SITEMAP_EMAIL_TO=
PROJECT_CONTEXT_PROMPT_DIR=./private
LOGS_DIR=/var/log/agent-monitoring
POSTGRES_DATA_DIR=/var/lib/agent-monitoring/postgresql
```

### 3. Build The Release Image

Use the Git tag created by the release workflow or the tag you are deploying:

```bash
TAG=v1.2.3 doppler run -- infra/scripts/release/build.sh
```

The image name is:

```text
prod-agent-monitoring:<TAG>
```

### 4. First VPS Deploy

Use this only for a brand-new production database directory:

```bash
ALLOW_EMPTY_POSTGRES_DATA_DIR=true \
SKIP_BACKUP=true \
TAG=v1.2.3 \
doppler run -- infra/scripts/release/deploy.sh
```

This lets Postgres initialize the empty data directory, then the deploy script
runs migrations and starts the selected one-shot monitoring command.

Use the first-init flags once. After
`$POSTGRES_DATA_DIR/data/pgdata/PG_VERSION` exists, use normal deploys.

### 5. Normal Deploy

```bash
TAG=v1.2.3 doppler run -- infra/scripts/release/deploy.sh
```

For non-interactive automation:

```bash
AUTO_APPROVE=true TAG=v1.2.3 doppler run -- infra/scripts/release/deploy.sh
```

The deploy script:

- validates the production Compose config
- verifies the tagged image exists
- creates a database backup unless `SKIP_BACKUP=true`
- starts the database service
- creates the app log directory
- runs committed migrations unless `SKIP_MIGRATE=true`
- runs the selected monitoring command, defaulting to `log_analysis`
- writes the deployed tag to `/var/lib/agent-monitoring/prod/current_tag`

Run sitemap analysis during deploy instead of log analysis:

```bash
MONITORING_COMMAND=sitemap-analysis TAG=v1.2.3 doppler run -- infra/scripts/release/deploy.sh
```

### 6. Manual VPS Checks

After deploy, check MCP:

```bash
TAG="$(cat /var/lib/agent-monitoring/prod/current_tag)" \
doppler run -- docker compose -f docker-compose.prod.yml run --rm app check-mcp
```

Run log analysis manually:

```bash
TAG="$(cat /var/lib/agent-monitoring/prod/current_tag)" \
doppler run -- docker compose -f docker-compose.prod.yml run --rm app log_analysis --force --email
```

Run sitemap analysis manually:

```bash
TAG="$(cat /var/lib/agent-monitoring/prod/current_tag)" \
doppler run -- docker compose -f docker-compose.prod.yml run --rm app \
  sitemap-analysis --force --email
```

Use `--force` only when replacing the existing report for the same date is
intentional. Use `--no-email` for a persisted dry run without email.

### 7. Install Cron

Cron templates and installation live in the separate `devops` repository:

```bash
cd /devops
git pull
bash cron/agent-monitoring.sh
```

Cron logs are written to:

```text
/var/log/devops/cron/agent-monitoring/log-analysis.log
/var/log/devops/cron/agent-monitoring/sitemap-analysis.log
```

App logger files are written to:

```text
/var/log/agent-monitoring/YYYY-MM-DD.jsonl
```

## Local Runtime

Use Docker Compose for local DB-backed jobs:

```bash
docker compose run --rm monitoring-app check-mcp
docker compose run --rm monitoring-app log_analysis
docker compose run --rm monitoring-app sitemap-analysis
```

Run local jobs with explicit rerun and email behavior:

```bash
doppler run -- docker compose run --rm monitoring-app log_analysis --force --email
doppler run -- docker compose run --rm monitoring-app sitemap-analysis --force --email
```

Useful flags:

- `--force`: replace an existing report for the same analysis date.
- `--email`: send the report email after the job succeeds.
- `--no-email`: run and persist the report without sending email.
- `--compare-history`: compare current grouped errors with the latest saved
  successful log-analysis run before the LLM call.
- `--no-compare-history`: disable the Python history comparison shortcut.

Local Compose runs migrations before monitoring commands. It uses:

```text
DATABASE_HOST=127.0.0.1
DATABASE_PORT=5438
DATABASE_NAME=monitoring
DATABASE_USER=monitoring
DATABASE_PASSWORD=monitoring
MCP_URL=http://127.0.0.1:8001/mcp
```

Local app logs are written to `logs/YYYY-MM-DD.jsonl`.

Override the MCP endpoint when needed:

```bash
MCP_URL=https://mcp.example.com/mcp docker compose run --rm monitoring-app check-mcp
```

## Developer Commands

Host-side `uv run` commands are developer shortcuts. They require the same DB,
MCP, OpenAI, and email settings from `.env`, Doppler, or the shell.

```bash
uv run makemigrations add_monitoring_models
uv run migrate
uv run pytest
uv run ruff check src
uv run black --check src
uv run mypy --explicit-package-bases src
```

`uv run makemigrations <name>` generates Aerich migration files offline.

## CI/CD

GitHub Actions workflows live in `.github/workflows`:

- `ci.yml`: quality checks, tests, CodeQL, version check, Docker build smoke
- `codeql.yml`: scheduled CodeQL scan
- `release.yml`: tags `main` through the shared release workflow

Operational scripts live under `infra/scripts`:

```bash
TAG=v1.2.3 infra/scripts/release/build.sh
TAG=v1.2.3 infra/scripts/release/deploy.sh
```

Production Postgres data is stored at
`/var/lib/agent-monitoring/postgresql` by default. Override with
`POSTGRES_DATA_DIR` only when pointing to another durable host path.

## Notes For Developers

Keep detailed architecture and agent-facing guidance out of this README. Use
`AGENTS.md` for repository guidance and `infra/docs/` for deeper design notes.

Useful docs:

- `AGENTS.md`
- `infra/scripts/README.md`
- `infra/docs/log_analysis_history_comparison.md`
- `infra/docs/vps_monitoring_context.example.md`
