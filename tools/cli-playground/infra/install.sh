#!/usr/bin/env bash
set -euo pipefail
[[ $# == 0 ]] || exit 2
owner=${CLIS_PLAYGROUND_DIR:-/opt/clis/playground}
config=${CLIS_CONFIG_DIR:-/etc/clis}
if ! docker network inspect playground-proxy >/dev/null 2>&1; then docker network create playground-proxy >/dev/null; fi
for volume in playground-data playground-certificates; do
    if ! docker volume inspect "$volume" >/dev/null 2>&1; then docker volume create "$volume" >/dev/null; fi
done
timeout 180 docker compose --env-file "$config/playground.env" --env-file "$config/playground-image.env" \
    -f "$owner/compose.production.yml" up -d --no-build --wait --wait-timeout 150
