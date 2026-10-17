#!/usr/bin/env bash
# Change the approved sandbox profile and reload only its orchestrator.
set -euo pipefail
[[ $# == 1 && $1 =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
image=$1
config=${CLIS_CONFIG_DIR:-/etc/clis}
playground=${CLIS_PLAYGROUND_DIR:-/opt/clis/playground}
boxie=${CLIS_BOXIE_DIR:-/opt/clis/boxie}
docker pull "$image"
PLAYGROUND_SANDBOX_IMAGE=$image python3 "$playground/playground_infra/installation.py" "$playground" --profile-only
compose() {
    docker compose --env-file "$config/boxie.env" --env-file "$config/boxie-image.env" \
        -f "$boxie/compose.production.yml" -f "$boxie/compose.observability.yml" "$@"
}
compose stop --timeout 60 orchestrator
compose up -d --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 150 orchestrator
