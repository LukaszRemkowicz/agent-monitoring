#!/usr/bin/env bash
###############################################################################
# release.sh
#
# Purpose:
#   Build and deploy one tagged production agent-monitoring release.
#
# Typical usage:
#   TAG=v0.1.0 doppler run -- infra/scripts/release/release.sh
#   AUTO_APPROVE=true TAG=v0.1.0 doppler run -- infra/scripts/release/release.sh
#   TAG=v0.1.0 doppler run -- infra/scripts/release/release.sh --emergency
#
# What this script does:
#   - validates the release environment and tag
#   - runs release/build.sh
#   - runs release/deploy.sh
#
# What this script does not do:
#   - does not duplicate build or deploy internals
#   - does not bypass deploy backup, migration, or confirmation behavior
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils.sh
source "$SCRIPT_DIR/../utils.sh"

PROJECT_DIR="$(get_project_dir)"
ENVIRONMENT="$(normalize_environment "${ENVIRONMENT:-prod}")"
validate_release_environment "$ENVIRONMENT"

EMERGENCY="${EMERGENCY:-false}"

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --emergency)
            EMERGENCY="true"
            ;;
        *)
            log_error "Unknown release option: $1"
            log_info "Usage: TAG=v1.2.3 infra/scripts/release/release.sh [--emergency]"
            exit 1
            ;;
    esac
    shift
done

TAG="${TAG:-$(git -C "$PROJECT_DIR" describe --tags --exact-match 2>/dev/null || true)}"
validate_tag "$TAG"
export ENVIRONMENT TAG EMERGENCY

log_header "Releasing ${ENVIRONMENT}-agent-monitoring:${TAG}"
log_info "Environment: $ENVIRONMENT"
log_info "Release tag: $TAG"
log_info "Project root: $PROJECT_DIR"
if [[ "$EMERGENCY" == "true" ]]; then
    log_warn "Emergency mode enabled for release build."
fi

log_step 1 2 "Build release image"
"$SCRIPT_DIR/build.sh"

log_step 2 2 "Deploy release image"
"$SCRIPT_DIR/deploy.sh"

log_success "Release complete: ${ENVIRONMENT}-agent-monitoring:${TAG}"
