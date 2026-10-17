#!/usr/bin/env bash
set -euo pipefail
release=$1
if ! docker network inspect playground-proxy >/dev/null 2>&1; then docker network create playground-proxy >/dev/null; fi
for volume in playground-data playground-certificates; do
    if ! docker volume inspect "$volume" >/dev/null 2>&1; then docker volume create "$volume" >/dev/null; fi
done
timeout 180 docker compose --env-file /etc/clis/playground.env --env-file "$release/image.env" \
    -f "$release/tools/cli-playground/infra/compose.production.yml" up -d --no-build --wait --wait-timeout 150
