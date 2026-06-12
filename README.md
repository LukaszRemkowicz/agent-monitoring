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

### 1. Prepare Host Directories

Run this once on the VPS before the first deploy:

```bash
sudo mkdir -p /var/lib/agent-monitoring/postgresql
sudo chown -R "$(id -un):$(id -gn)" /var/lib/agent-monitoring

sudo mkdir -p /var/log/agent-monitoring
sudo chown -R "$(id -un):$(id -gn)" /var/log/agent-monitoring
```

These directories are outside the checkout, so the deploy script expects them
to be writable by the production user.

### 2. Prepare Project Context Prompt

Create the project context prompt file on the VPS:

```bash
mkdir -p private
$EDITOR private/vps_monitoring_context.md
```

If the real file lives in the private `devops` checkout, set:

```bash
PROJECT_CONTEXT_PROMPT_PATH=/home/lukasz/devops/agent-monitoring/vps_monitoring_context.md
```

This file is required for `log_analysis`. It should describe the VPS services,
domains, ports, and operational expectations that the report needs to understand.

The `private/` directory is ignored by Git.

### 3. Configure Secrets

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
SITEMAP_PUBLIC_HOST=example.com
```

Optional values:

```bash
LLM_DEFAULT_MODEL=gpt-4.1-mini
LLM_FAST_MODEL=gpt-4.1-mini
LLM_STRONG_MODEL=gpt-5
SITEMAP_EMAIL_TO=
PROJECT_CONTEXT_PROMPT_PATH=/home/lukasz/devops/agent-monitoring/vps_monitoring_context.md
LOGS_DIR=/var/log/agent-monitoring
POSTGRES_DATA_DIR=/var/lib/agent-monitoring/postgresql
```

### 4. Release

Build and deploy the Git tag created by the release workflow or the tag you are
deploying:

```bash
TAG=v1.2.3 doppler run -- infra/scripts/release/release.sh
```

The image name is:

```text
prod-agent-monitoring:<TAG>
```

For non-interactive automation:

```bash
AUTO_APPROVE=true TAG=v1.2.3 doppler run -- infra/scripts/release/release.sh
```

To build and deploy as separate manual steps:

```bash
TAG=v1.2.3 doppler run -- infra/scripts/release/build.sh
TAG=v1.2.3 doppler run -- infra/scripts/release/deploy.sh
```

The deploy script:

- validates the production Compose config
- verifies the tagged image exists
- creates a database backup unless `SKIP_BACKUP=true`
- starts the database service
- creates the app log directory
- runs committed migrations unless `SKIP_MIGRATE=true`
- writes the deployed tag to `/var/lib/agent-monitoring/prod/current_tag`

Deploy does not run log or sitemap analysis automatically. Run monitoring jobs
on demand after deploy with the commands below.

### 6. Production Ad Hoc Commands

Run these from the production `agent-monitoring` checkout. They use the deployed
image tag recorded by the last successful deploy.

Check MCP:

```bash
doppler run -- uv run typer check-mcp
```

Run migrations:

```bash
doppler run -- uv run migrate
```

Run log analysis:

```bash
doppler run -- uv run typer log-analysis --force --email
```

Run sitemap analysis:

```bash
doppler run -- uv run typer sitemap-analysis --force --email
```

Use `--force` only when replacing the existing report for the same date is
intentional. Use `--no-email` for a persisted dry run without email. On the VPS,
`uv run migrate`, `uv run shell`, and `uv run typer ...` read
`/var/lib/agent-monitoring/prod/current_tag` and run the deployed image through
`docker-compose.prod.yml`. On local machines, `uv run migrate` runs Aerich
directly and `uv run typer ...` runs the Typer command locally.
The prod Compose file declares `name: agent-monitoring`, so ad hoc jobs and
deploy use the same database container namespace.

Inspect stored reports without rerunning analysis:

```bash
doppler run -- uv run typer reports log list --limit 5

doppler run -- uv run typer reports log show --date YYYY-MM-DD

doppler run -- uv run typer reports sitemap list --limit 5

doppler run -- uv run typer reports sitemap show --date YYYY-MM-DD

doppler run -- uv run typer reports attention --limit 10
```

Add `--json` to any `uv run typer reports ...` command when Codex or another
tool should consume the output. Use `uv run typer reports --help` and subcommand
`--help` for the full option list.

Stored log reports keep summaries, findings, evidence fingerprints, and MCP
artifact references. Raw logs stay in MCP-owned artifacts and are not copied
into this app. If MCP artifact retention expires an old raw-log artifact, the
stored report remains useful for review and trend history, but raw follow-up
from the MCP reference may no longer resolve. `uv run typer reports log show`
prints this MCP artifact retention notice alongside the follow-up hints.

Clean up stored monitoring DB rows after the configured retention window:

```bash
doppler run -- uv run typer cleanup reports

doppler run -- uv run typer cleanup reports --confirm
```

The cleanup command is a dry run unless `--confirm` is provided. It uses
`RETENTION_DAYS` as the shared fallback, with
`LOG_ANALYSIS_RETENTION_DAYS` and `SITEMAP_ANALYSIS_RETENTION_DAYS` available
when the report categories need different windows. It deletes old log reports
and sitemap reports, but preserves the most recent successful log-analysis
history rows needed for trend comparison. That protected count defaults to
`LOG_ANALYSIS_PROTECTED_HISTORY_COUNT=5`.

The report cleanup command does not delete log-analysis LLM/tool-call audit
rows. `CRITICAL`, failed, and unsent rows currently use the same category
cutoff as other report rows unless they are part of protected successful log
history. Add `--json` for machine-readable output.

### 7. Install Cron

Cron templates and installation live in the separate `devops` repository:

```bash
cd ~/devops
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
uv run typer check-mcp
uv run migrate
uv run typer log-analysis
uv run typer sitemap-analysis
```

Run local jobs with explicit rerun and email behavior:

```bash
doppler run -- uv run typer log-analysis --force --email
doppler run -- uv run typer sitemap-analysis --force --email
```

Inspect local stored reports:

```bash
docker compose run --rm monitoring-app uv run typer reports log list --limit 5
docker compose run --rm monitoring-app uv run typer reports log show --date YYYY-MM-DD
docker compose run --rm monitoring-app uv run typer reports sitemap list --limit 5
docker compose run --rm monitoring-app uv run typer reports sitemap show --date YYYY-MM-DD
docker compose run --rm monitoring-app uv run typer reports attention --limit 10 --json
```

Dry-run and confirm local retention cleanup:

```bash
docker compose run --rm monitoring-app uv run typer cleanup reports
docker compose run --rm monitoring-app uv run typer cleanup reports \
  --log-retention-days 90 --sitemap-retention-days 30
docker compose run --rm monitoring-app uv run typer cleanup reports --confirm
```

Useful flags:

- `--force`: replace an existing report for the same analysis date.
- `--email`: send the report email after the job succeeds.
- `--no-email`: run and persist the report without sending email.
- `--confirm`: delete retention cleanup candidates; cleanup commands dry-run
  without it.
- `--retention-days`: shared fallback retention window for report cleanup.
- `--log-retention-days`: log-analysis report cleanup window.
- `--sitemap-retention-days`: sitemap-analysis report cleanup window.
- `--protected-log-history-count`: recent successful log-analysis rows to keep
  for trend history.
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
TAG=v1.2.3 infra/scripts/release/release.sh
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
