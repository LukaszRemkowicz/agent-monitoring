#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils.sh
source "$SCRIPT_DIR/../utils.sh"

PROJECT_DIR="$(get_project_dir)"
if [[ -z "${ENVIRONMENT:-}" ]]; then
    log_error "ENVIRONMENT is required. Usage: ENVIRONMENT=local|prod $0"
    exit 1
fi
ENVIRONMENT="$(normalize_environment "$ENVIRONMENT")"
COMPOSE_FILE="${COMPOSE_FILE:-$(get_compose_file "$PROJECT_DIR" "$ENVIRONMENT")}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(get_compose_project_name "$ENVIRONMENT")}"
BACKUP_DIR="$(get_backup_dir "$PROJECT_DIR" "$ENVIRONMENT")"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

DATABASE_NAME="${DATABASE_NAME:-agent_monitoring}"
DATABASE_USER="${DATABASE_USER:-agent_monitoring}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-local-secret}"
POSTGRES_DATA_DIR="${POSTGRES_DATA_DIR:-/var/lib/agent-monitoring/postgresql}"

export ENVIRONMENT COMPOSE_PROJECT_NAME DATABASE_NAME DATABASE_USER DATABASE_PASSWORD POSTGRES_DATA_DIR
export TAG="${TAG:-backup}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$BACKUP_DIR/agent_monitoring_${ENVIRONMENT}_${timestamp}.dump"
tmp_file="$backup_file.tmp"
lock_dir="$BACKUP_DIR/.backup.lock"

cleanup() {
    rm -f "$tmp_file"
    rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"

log_step 1 8 "Acquire backup lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
    log_error "Another backup is already running: $lock_dir"
    exit 1
fi

log_header "Starting $ENVIRONMENT database backup"
log_info "Backup file: $backup_file"

log_step 2 8 "Ensure database service is running"
docker compose -f "$COMPOSE_FILE" up -d db

log_step 3 8 "Wait for PostgreSQL readiness"
for attempt in {1..30}; do
    if docker compose -f "$COMPOSE_FILE" exec -T db \
        pg_isready --host=127.0.0.1 --username="$DATABASE_USER" --dbname="$DATABASE_NAME" \
        >/dev/null 2>&1; then
        break
    fi

    if [[ "$attempt" -eq 30 ]]; then
        log_error "Database did not become ready for backup."
        exit 1
    fi

    sleep 2
done

log_step 4 8 "Create custom-format database dump"
docker compose -f "$COMPOSE_FILE" exec -T db \
    env PGPASSWORD="$DATABASE_PASSWORD" \
    pg_dump \
        --host=127.0.0.1 \
        --format=custom \
        --no-owner \
        --no-privileges \
        --username="$DATABASE_USER" \
        "$DATABASE_NAME" \
    > "$tmp_file"

log_step 5 8 "Verify backup file is not empty"
if [[ ! -s "$tmp_file" ]]; then
    log_error "Backup file is empty."
    exit 1
fi

log_step 6 8 "Validate backup dump"
docker compose -f "$COMPOSE_FILE" exec -T db pg_restore --list < "$tmp_file" >/dev/null

log_step 7 8 "Promote validated backup"
mv "$tmp_file" "$backup_file"

log_step 8 8 "Prune backups older than $RETENTION_DAYS days"
find "$BACKUP_DIR" \
    -type f \
    -name "agent_monitoring_${ENVIRONMENT}_*.dump" \
    -mtime "+$RETENTION_DAYS" \
    -delete

log_success "Backup complete: $backup_file"
