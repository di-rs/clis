#!/usr/bin/env bash
set -euo pipefail
[[ $# == 0 ]] || exit 2
owner=${SANCTUARY_CLI_PLAYGROUND_DIR:-/opt/sanctuary/cli-playground}
config=${SANCTUARY_CONFIG_DIR:-/etc/sanctuary}
if ! docker network inspect cli-playground-proxy >/dev/null 2>&1; then docker network create cli-playground-proxy >/dev/null; fi
for volume in cli-playground-data cli-playground-certificates; do
    if ! docker volume inspect "$volume" >/dev/null 2>&1; then docker volume create "$volume" >/dev/null; fi
done
bash "$owner/provision.sh"
timeout 180 docker compose --env-file "$config/cli-playground.env" --env-file "$config/cli-playground-image.env" \
    -f "$owner/compose.production.yml" run --rm --no-deps backend python -m cli_playground.migrate --dsn-file /run/secrets/postgres-dsn
timeout 180 docker compose --env-file "$config/cli-playground.env" --env-file "$config/cli-playground-image.env" \
    -f "$owner/compose.production.yml" up -d --no-build --wait --wait-timeout 150
