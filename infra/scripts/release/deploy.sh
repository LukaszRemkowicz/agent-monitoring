#!/usr/bin/env bash
###############################################################################
# deploy.sh
#
# Purpose:
#   Deploy one prepared agent-monitoring production image safely and repeatedly.
#
# Typical usage:
#   TAG=v0.1.0 infra/scripts/release/deploy.sh
#   AUTO_APPROVE=true TAG=v0.1.0 infra/scripts/release/deploy.sh
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils.sh
source "$SCRIPT_DIR/../utils.sh"

PROJECT_DIR="$(get_project_dir)"
ENVIRONMENT="$(normalize_environment "${ENVIRONMENT:-prod}")"
validate_release_environment "$ENVIRONMENT"

TAG="${TAG:-$(git -C "$PROJECT_DIR" describe --tags --exact-match 2>/dev/null || true)}"
validate_tag "$TAG"

COMPOSE_FILE="${COMPOSE_FILE:-$(get_compose_file "$PROJECT_DIR" "$ENVIRONMENT")}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(get_compose_project_name "$ENVIRONMENT")}"
IMAGE_NAME="${ENVIRONMENT}-agent-monitoring:${TAG}"
STATE_DIR="$(get_state_dir "$ENVIRONMENT")"
LOCK_DIR="$STATE_DIR/deploy.lock"

SKIP_BACKUP="${SKIP_BACKUP:-false}"
SKIP_MIGRATE="${SKIP_MIGRATE:-false}"
DRY_RUN="${DRY_RUN:-false}"
MONITORING_COMMAND="${MONITORING_COMMAND:-log_analysis}"

DATABASE_NAME="${DATABASE_NAME:-agent_monitoring}"
DATABASE_USER="${DATABASE_USER:-agent_monitoring}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:?DATABASE_PASSWORD is required}"
LOG_ANALYSIS_MCP_URL="${LOG_ANALYSIS_MCP_URL:?LOG_ANALYSIS_MCP_URL is required}"
MCP_WORKFLOW_JWT="${MCP_WORKFLOW_JWT:?MCP_WORKFLOW_JWT is required}"
MONITORING_PROJECT="${MONITORING_PROJECT:-landingpage}"
EMAIL_HOST="${EMAIL_HOST:?EMAIL_HOST is required}"
EMAIL_PORT="${EMAIL_PORT:-25}"
EMAIL_USERNAME="${EMAIL_USERNAME:-}"
EMAIL_PASSWORD="${EMAIL_PASSWORD:-}"
EMAIL_FROM="${EMAIL_FROM:?EMAIL_FROM is required}"
EMAIL_TO="${EMAIL_TO:?EMAIL_TO is required}"
SITEMAP_ROOT_URL="${SITEMAP_ROOT_URL:-}"
SITEMAP_EMAIL_TO="${SITEMAP_EMAIL_TO:-}"
RETENTION_DAYS="${RETENTION_DAYS:-90}"

export \
    ENVIRONMENT \
    TAG \
    COMPOSE_PROJECT_NAME \
    DATABASE_NAME \
    DATABASE_USER \
    DATABASE_PASSWORD \
    LOG_ANALYSIS_MCP_URL \
    MCP_WORKFLOW_JWT \
    MONITORING_PROJECT \
    EMAIL_HOST \
    EMAIL_PORT \
    EMAIL_USERNAME \
    EMAIL_PASSWORD \
    EMAIL_FROM \
    EMAIL_TO \
    SITEMAP_ROOT_URL \
    SITEMAP_EMAIL_TO \
    RETENTION_DAYS

cleanup() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

deploy_step() {
    local icon="$1"
    local current="$2"
    local total="$3"
    local message="$4"

    printf "\n%s [DEPLOY] [%s/%s] %s\n" "$icon" "$current" "$total" "$message"
}

mkdir -p "$STATE_DIR"

printf "\n🚀 Deploying %s\n" "$IMAGE_NAME"
printf "⚙️  Environment: %s\n" "$ENVIRONMENT"
printf "🏷️  Release tag: %s\n" "$TAG"
printf "📦 Compose project: %s\n" "$COMPOSE_PROJECT_NAME"
printf "🧾 Compose file: %s\n" "$COMPOSE_FILE"
printf "🧪 Monitoring command: %s\n" "$MONITORING_COMMAND"
printf "📁 State directory: %s\n" "$STATE_DIR"

COMPOSE_ARGS=(-f "$COMPOSE_FILE")

deploy_step "🔒" 1 8 "Acquire deploy lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log_error "Another deployment is already running: $LOCK_DIR"
    exit 1
fi
printf "✅ Deploy lock acquired: %s\n" "$LOCK_DIR"

deploy_step "🧪" 2 8 "Check dry-run mode and validate Compose config"
if [[ "$DRY_RUN" == "true" ]]; then
    printf "🧾 DRY RUN: validating Compose config only\n"
    docker compose "${COMPOSE_ARGS[@]}" config >/dev/null
    printf "✅ Dry run complete\n"
    exit 0
fi
docker compose "${COMPOSE_ARGS[@]}" config >/dev/null
printf "✅ Compose config validated\n"

deploy_step "🔍" 3 8 "Verify release image exists"
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    log_error "Image not found locally: $IMAGE_NAME"
    log_info "Build it first with TAG=$TAG infra/scripts/release/build.sh"
    exit 1
fi
printf "✅ Release image found: %s\n" "$IMAGE_NAME"

deploy_step "⚠️" 4 8 "Confirm deploy"
confirm_continue "Type yes to deploy $IMAGE_NAME to $ENVIRONMENT."
printf "✅ Deploy confirmed\n"

deploy_step "💾" 5 8 "Run pre-deploy database backup"
if [[ "$SKIP_BACKUP" != "true" ]]; then
    ENVIRONMENT="$ENVIRONMENT" \
    TAG="$TAG" \
    COMPOSE_FILE="$COMPOSE_FILE" \
    COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    DATABASE_NAME="$DATABASE_NAME" \
    DATABASE_USER="$DATABASE_USER" \
    DATABASE_PASSWORD="$DATABASE_PASSWORD" \
        "$PROJECT_DIR/infra/scripts/db_backup/backup_db.sh"
else
    log_warn "Skipping database backup because SKIP_BACKUP=true"
    confirm_continue "Type yes to continue without a pre-deploy backup."
fi

deploy_step "🐘" 6 8 "Ensure database service is running"
docker compose "${COMPOSE_ARGS[@]}" up -d db
printf "✅ Database service is running or starting\n"

deploy_step "🧬" 7 8 "Apply database migrations"
if [[ "$SKIP_MIGRATE" != "true" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" run --rm app uv run migrate
    printf "✅ Database migrations applied\n"
else
    log_warn "Skipping database migrations because SKIP_MIGRATE=true"
    confirm_continue "Type yes to continue without applying migrations."
fi

deploy_step "🚀" 8 8 "Run monitoring command"
docker compose "${COMPOSE_ARGS[@]}" run --rm app "$MONITORING_COMMAND"
printf "%s\n" "$TAG" > "$STATE_DIR/current_tag"
printf "✅ Monitoring command completed\n"
printf "🎉 Deploy complete: %s\n" "$IMAGE_NAME"
