#!/usr/bin/env bash
###############################################################################
# build.sh
#
# Purpose:
#   Build the tagged production agent-monitoring image in a deterministic,
#   rollback-friendly way.
#
# Typical usage:
#   TAG=v0.1.0 infra/scripts/release/build.sh
#   NO_CACHE=true TAG=v0.1.0 infra/scripts/release/build.sh
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

IMAGE_NAME="${ENVIRONMENT}-agent-monitoring:${TAG}"
STATE_DIR="$(get_state_dir "$ENVIRONMENT")"
NO_CACHE="${NO_CACHE:-false}"
EMERGENCY="${EMERGENCY:-false}"

mkdir -p "$STATE_DIR"

log_header "Building $IMAGE_NAME"
log_info "Environment: $ENVIRONMENT"
log_info "Release tag: $TAG"
log_info "Project root: $PROJECT_DIR"
log_info "State directory: $STATE_DIR"
if [[ "$NO_CACHE" == "true" ]]; then
    log_info "No-cache mode enabled (fresh build)"
fi

log_step 1 6 "Check working tree"
if [[ "$EMERGENCY" != "true" ]] && [[ -n "$(git -C "$PROJECT_DIR" status --porcelain)" ]]; then
    log_error "Working tree has uncommitted changes. Set EMERGENCY=true to build anyway."
    git -C "$PROJECT_DIR" status --short
    exit 1
fi

log_step 2 6 "Prepare Docker build arguments"
build_args=(--pull -f "$PROJECT_DIR/Dockerfile" --target production -t "$IMAGE_NAME")

if [[ "$NO_CACHE" == "true" ]]; then
    build_args+=(--no-cache)
fi

log_step 3 6 "Build tagged app image"
docker build "${build_args[@]}" "$PROJECT_DIR"
log_success "Image built: $IMAGE_NAME"

log_step 4 6 "Verify built image exists"
docker image inspect "$IMAGE_NAME" >/dev/null
log_success "Image available locally: $IMAGE_NAME"

log_step 5 6 "Record built tag"
printf "%s\n" "$TAG" > "$STATE_DIR/built_tag"
log_info "Built tag file: $STATE_DIR/built_tag"

log_step 6 6 "Prune older local images"
prune_local_images "${ENVIRONMENT}-agent-monitoring" "$TAG"

log_success "Build complete: $IMAGE_NAME"
