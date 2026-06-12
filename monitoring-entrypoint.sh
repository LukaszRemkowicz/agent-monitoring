#!/usr/bin/env sh
set -eu

# Docker image entrypoint for the monitoring app.
#
# Docker calls this script before the container command from docker-compose.
# Its job is intentionally small:
#
# 1. For app-level commands (`typer ...` and `shell ...`), optionally run
#    database migrations first when MONITORING_AUTO_MIGRATE=true.
#    Local compose enables this so developer commands start against the current
#    schema. Production can keep it disabled and run `uv run migrate` explicitly
#    during deploy.
#
# 2. If migrations fail, stop before the requested monitoring command starts.
#    That prevents log/sitemap jobs from running against a stale or broken DB
#    schema and makes the migration error the visible failure.
#
# 3. Replace this shell process with `uv run "$@"`.
#    Examples:
#      command: ["typer", "log-analysis"] -> uv run typer log-analysis
#      command: ["shell"]                 -> uv run shell
#      command: ["migrate"]               -> uv run migrate
#
# This file is not business logic and should stay as the container bootstrap
# boundary only. CLI command behavior lives in src/cli/.

case "${1:-}" in
    typer|shell)
        if [ "${MONITORING_AUTO_MIGRATE:-false}" = "true" ]; then
            echo "Applying database migrations before running $1..."
            if uv run migrate; then
                :
            else
                status=$?
                echo
                echo "Database migrations failed. The monitoring command was not started."
                echo "Run 'docker compose run --rm monitoring-app migrate' or 'uv run migrate' to inspect the migration error."
                exit "$status"
            fi
        fi
        ;;
esac

exec uv run "$@"
