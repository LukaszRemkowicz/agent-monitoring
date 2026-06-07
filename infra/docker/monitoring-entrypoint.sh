#!/usr/bin/env sh
set -eu

case "${1:-}" in
    log_analysis|sitemap-analysis|check-mcp|shell)
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
