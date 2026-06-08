#!/usr/bin/env bash
###############################################################################
# deploy.sh
#
# Purpose:
#   Deploy one prepared agent-monitoring production image safely and repeatedly.
#
# Typical usage:
#   TAG=v0.1.0 doppler run -- infra/scripts/release/deploy.sh
#   AUTO_APPROVE=true TAG=v0.1.0 doppler run -- infra/scripts/release/deploy.sh
#
# What this script does:
#   - validates Compose configuration and required production secrets
#   - prevents concurrent deploys with a lock
#   - verifies the tagged image exists locally
#   - runs a pre-deploy database backup unless SKIP_BACKUP=true
#   - applies migrations unless SKIP_MIGRATE=true
#   - runs the selected one-shot monitoring command
#   - records current_tag after the command completes
#
# What this script does not do:
#   - does not build images
#   - does not create or rotate secrets
#   - does not automatically rollback after a failed monitoring command
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
ALLOW_EMPTY_POSTGRES_DATA_DIR="${ALLOW_EMPTY_POSTGRES_DATA_DIR:-false}"

DATABASE_NAME="${DATABASE_NAME:?DATABASE_NAME is required}"
DATABASE_USER="${DATABASE_USER:?DATABASE_USER is required}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:?DATABASE_PASSWORD is required}"
MCP_URL="${MCP_URL:?MCP_URL is required}"
MCP_WORKFLOW_JWT="${MCP_WORKFLOW_JWT:?MCP_WORKFLOW_JWT is required}"
OPENAI_API_KEY="${OPENAI_API_KEY:?OPENAI_API_KEY is required}"
EMAIL_HOST="${EMAIL_HOST:-smtp.gmail.com}"
EMAIL_PORT="${EMAIL_PORT:-587}"
EMAIL_USERNAME="${EMAIL_USERNAME:?EMAIL_USERNAME is required}"
EMAIL_PASSWORD="${EMAIL_PASSWORD:?EMAIL_PASSWORD is required}"
EMAIL_FROM="${EMAIL_FROM:?EMAIL_FROM is required}"
EMAIL_TO="${EMAIL_TO:?EMAIL_TO is required}"
SITE_DOMAIN="${SITE_DOMAIN:?SITE_DOMAIN is required}"
SITEMAP_EMAIL_TO="${SITEMAP_EMAIL_TO:-}"
RETENTION_DAYS="${RETENTION_DAYS:-90}"
POSTGRES_DATA_DIR="${POSTGRES_DATA_DIR:-/var/lib/agent-monitoring/postgresql}"
POSTGRES_PG_VERSION_FILE="$POSTGRES_DATA_DIR/data/pgdata/PG_VERSION"
PROJECT_CONTEXT_PROMPT_PATH="${PROJECT_CONTEXT_PROMPT_PATH:-$PROJECT_DIR/private/vps_monitoring_context.md}"
LOGS_DIR="${LOGS_DIR:-/var/log/agent-monitoring}"

export \
    ENVIRONMENT \
    TAG \
    COMPOSE_PROJECT_NAME \
    DATABASE_NAME \
    DATABASE_USER \
    DATABASE_PASSWORD \
    MCP_URL \
    MCP_WORKFLOW_JWT \
    OPENAI_API_KEY \
    EMAIL_HOST \
    EMAIL_PORT \
    EMAIL_USERNAME \
    EMAIL_PASSWORD \
    EMAIL_FROM \
    EMAIL_TO \
    SITE_DOMAIN \
    SITEMAP_EMAIL_TO \
    RETENTION_DAYS \
    POSTGRES_DATA_DIR \
    PROJECT_CONTEXT_PROMPT_PATH \
    LOGS_DIR

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

ensure_writable_dir() {
    local label="$1"
    local path="$2"
    local owner
    local group

    if mkdir -p "$path" 2>/dev/null; then
        printf "✅ %s exists: %s\n" "$label" "$path"
        return
    fi

    owner="$(id -un)"
    group="$(id -gn)"
    log_error "Cannot create $label: $path"
    log_info "Run this once on the VPS, then retry deploy:"
    log_info "sudo mkdir -p '$path'"
    log_info "sudo chown -R '$owner:$group' '$path'"
    exit 1
}

ensure_readable_file() {
    local label="$1"
    local path="$2"

    if [[ -r "$path" ]]; then
        printf "✅ %s exists: %s\n" "$label" "$path"
        return
    fi

    log_error "$label is missing or not readable: $path"
    log_info "Create the file or set PROJECT_CONTEXT_PROMPT_PATH to the real host path."
    exit 1
}

mkdir -p "$STATE_DIR"

printf "\n🚀 Deploying %s\n" "$IMAGE_NAME"
printf "⚙️  Environment: %s\n" "$ENVIRONMENT"
printf "🏷️  Release tag: %s\n" "$TAG"
printf "📦 Compose project: %s\n" "$COMPOSE_PROJECT_NAME"
printf "🧾 Compose file: %s\n" "$COMPOSE_FILE"
printf "🧪 Monitoring command: %s\n" "$MONITORING_COMMAND"
printf "🐘 Postgres data directory: %s\n" "$POSTGRES_DATA_DIR"
printf "🔐 Project context prompt file: %s\n" "$PROJECT_CONTEXT_PROMPT_PATH"
printf "🪵 App log directory: %s\n" "$LOGS_DIR"
printf "📁 State directory: %s\n" "$STATE_DIR"

COMPOSE_ARGS=(-f "$COMPOSE_FILE")

# Step 1: take a deploy lock so two deploys cannot mutate the stack at once.
deploy_step "🔒" 1 8 "Acquire deploy lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log_error "Another deployment is already running: $LOCK_DIR"
    exit 1
fi
printf "✅ Deploy lock acquired: %s\n" "$LOCK_DIR"

# Step 2: for dry runs, validate Compose interpolation and exit before changes.
deploy_step "🧪" 2 8 "Check dry-run mode and validate Compose config"
if [[ "$DRY_RUN" == "true" ]]; then
    printf "🧾 DRY RUN: validating Compose config only\n"
    docker compose "${COMPOSE_ARGS[@]}" config >/dev/null
    printf "✅ Dry run complete\n"
    exit 0
fi
docker compose "${COMPOSE_ARGS[@]}" config >/dev/null
printf "✅ Compose config validated\n"
ensure_writable_dir "Postgres data directory" "$POSTGRES_DATA_DIR"
ensure_readable_file "Project context prompt file" "$PROJECT_CONTEXT_PROMPT_PATH"
ensure_writable_dir "App log directory" "$LOGS_DIR"
if [[ ! -f "$POSTGRES_PG_VERSION_FILE" && "$ALLOW_EMPTY_POSTGRES_DATA_DIR" != "true" ]]; then
    log_error "Postgres data directory is empty or not initialized: $POSTGRES_DATA_DIR"
    log_info "Expected marker file: $POSTGRES_PG_VERSION_FILE"
    log_info "Restore/migrate existing production data before deploying this compose file."
    log_info "For a brand-new environment only, set ALLOW_EMPTY_POSTGRES_DATA_DIR=true."
    exit 1
fi

# Step 3: verify the image was built or pulled before starting deployment.
deploy_step "🔍" 3 8 "Verify release image exists"
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    log_error "Image not found locally: $IMAGE_NAME"
    log_info "Build it first with TAG=$TAG infra/scripts/release/build.sh"
    exit 1
fi
printf "✅ Release image found: %s\n" "$IMAGE_NAME"

# Step 4: ask for explicit confirmation unless AUTO_APPROVE=true.
deploy_step "⚠️" 4 8 "Confirm deploy"
confirm_continue "Type yes to deploy $IMAGE_NAME to $ENVIRONMENT."
printf "✅ Deploy confirmed\n"

# Step 5: back up the target database before changing schema or running jobs.
deploy_step "💾" 5 8 "Run pre-deploy database backup"
if [[ "$SKIP_BACKUP" != "true" ]]; then
    ENVIRONMENT="$ENVIRONMENT" \
    TAG="$TAG" \
    COMPOSE_FILE="$COMPOSE_FILE" \
    COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    DATABASE_NAME="$DATABASE_NAME" \
    DATABASE_USER="$DATABASE_USER" \
    DATABASE_PASSWORD="$DATABASE_PASSWORD" \
    POSTGRES_DATA_DIR="$POSTGRES_DATA_DIR" \
        "$PROJECT_DIR/infra/scripts/db_backup/backup_db.sh"
else
    log_warn "Skipping database backup because SKIP_BACKUP=true"
    confirm_continue "Type yes to continue without a pre-deploy backup."
fi

# Step 6: make sure the database service is running before migrations.
deploy_step "🐘" 6 8 "Ensure database service is running"
docker compose "${COMPOSE_ARGS[@]}" up -d db
printf "✅ Database service is running or starting\n"

# Step 7: apply committed migrations from the release image.
deploy_step "🧬" 7 8 "Apply database migrations"
if [[ "$SKIP_MIGRATE" != "true" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" run --rm app migrate
    printf "✅ Database migrations applied\n"
else
    log_warn "Skipping database migrations because SKIP_MIGRATE=true"
    confirm_continue "Type yes to continue without applying migrations."
fi

# Step 8: run the selected one-shot monitoring command and record success.
deploy_step "🚀" 8 8 "Run monitoring command"
docker compose "${COMPOSE_ARGS[@]}" run --rm app "$MONITORING_COMMAND"
printf "%s\n" "$TAG" > "$STATE_DIR/current_tag"
printf "✅ Monitoring command completed\n"
printf "🎉 Deploy complete: %s\n" "$IMAGE_NAME"
