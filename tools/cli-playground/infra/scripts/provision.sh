#!/usr/bin/env bash
# Add Playground's database on either a new or an existing shared PostgreSQL host.
set -euo pipefail
[[ $# == 0 ]] || exit 2
config=${SANCTUARY_CONFIG_DIR:-/etc/sanctuary}
owner=${SANCTUARY_CLI_PLAYGROUND_DIR:-/opt/sanctuary/cli-playground}
boxie=${SANCTUARY_BOXIE_DIR:-/opt/sanctuary/boxie}
password=$(cat "$config/cli-playground/postgres-password")
[[ $password =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid Playground PostgreSQL password file' >&2; exit 1; }
# Reconcile the database-only network on existing installations before provisioning.
timeout 180 docker compose --env-file "$config/boxie.env" -f "$boxie/postgres/compose.yml" \
    up -d --no-build --wait --wait-timeout 150 postgres
{
    printf "\\set playground_password '%s'\n" "$password"
    cat "$owner/postgres/postgres.sql"
} | timeout 60 docker compose --env-file "$config/boxie.env" -f "$boxie/postgres/compose.yml" \
    exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1
