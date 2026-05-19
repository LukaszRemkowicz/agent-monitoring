# agent-monitoring

Standalone background monitoring app for the externalized log and sitemap
analysis workflow.

Phase 0 intentionally contains only the project skeleton:

- Python source layout directly under `src`
- explicit environment-backed settings
- Tortoise models and migration helpers under `src/db`
- Typer command entrypoints for `log_analysis`, `sitemap-analysis`, and
  `check-mcp`
- Docker Compose with PostgreSQL and a one-shot `monitoring-app` service
- pytest-based test runner

Runtime settings are exposed through `src/conf.py`, following
the Django-style `settings` pattern. Tortoise models live in
`src/db/models.py`. Tortoise-specific config and database URL
construction live in `src/db/config.py`. Database lifecycle
helpers and the application lifespan context manager live in
`src/db/lifecycle.py`, matching the MCP project split between
config and startup/shutdown. Migration command aliases live in
`src/db/cli.py`.

## Local Commands

```bash
uv run log_analysis --help
uv run sitemap-analysis --help
uv run check-mcp
uv run makemigrations add_monitoring_models
uv run pytest
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

## Docker Commands

```bash
docker compose run --rm monitoring-app log_analysis --help
docker compose run --rm monitoring-app migrate
docker compose run --rm monitoring-app pytest
```

Run migrations from the container so Aerich uses the Compose database service
via `DATABASE_HOST=db` and `DATABASE_PORT=5432`.

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
