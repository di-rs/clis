#!/usr/bin/env bash
# Initialize persistent dependencies and apply the Boxie image.
set -euo pipefail
[[ $# == 2 && $2 =~ ^[a-f0-9]{40}$ ]] || exit 2
source=$1
revision=$2
owner=${SANCTUARY_BOXIE_DIR:-/opt/sanctuary/boxie}
bash "$owner/credentials.sh" /etc/sanctuary/boxie
bash "$owner/certificates.sh" /etc/sanctuary/tls
python3 "$owner/boxie_infra/config.py" --output /etc/sanctuary/boxie --data-dir /srv/sanctuary/boxie \
    --installation-id "$(cat /etc/sanctuary/boxie/installation-id)" --host-id "$(cat /etc/sanctuary/boxie/host-id)" --consumer-id playground
export OBS_JOURNAL_GROUP_GID
OBS_JOURNAL_GROUP_GID=$(getent group systemd-journal | cut -d: -f3)
python3 "$owner/boxie_infra/installation.py" "$source"
install -o 472 -g 0 -m 0600 /etc/sanctuary/boxie/grafana-admin /etc/sanctuary/grafana-admin
chown 65532:65532 /etc/sanctuary/tunnel.json
chmod 0600 /etc/sanctuary/tunnel.json
set -a
# shellcheck disable=SC1091
source /etc/sanctuary/boxie.env
set +a
for network in boxie-control boxie-consumers boxie-metrics boxie-grafana-origin boxie-temporal-ui-origin; do
    if ! docker network inspect "$network" >/dev/null 2>&1; then docker network create --internal "$network" >/dev/null; fi
done
# Private networks are required even when a name already exists.
for network in boxie-control boxie-consumers boxie-metrics boxie-grafana-origin boxie-temporal-ui-origin; do
    [[ $(docker network inspect --format '{{.Internal}}' "$network") == true ]]
done
postgres() { timeout 180 docker compose --env-file /etc/sanctuary/boxie.env -f "$owner/postgres/compose.yml" "$@"; }
temporal() { timeout 180 docker compose --env-file /etc/sanctuary/boxie.env -f "$owner/temporal/compose.yml" -f "$owner/temporal/compose.observability.yml" "$@"; }
postgres up -d --no-build --wait --wait-timeout 150
# Initialization is explicit and never runs upstream schema upgrades on an installed stack.
if [[ ! -f /etc/sanctuary/temporal-schema-ready ]]; then
    for store in temporal visibility; do
        database=temporal
        [[ $store != visibility ]] || database=temporal_visibility
        present=$(postgres exec -T postgres psql -U postgres -d "$database" -Atc "SELECT to_regclass('public.schema_version') IS NOT NULL")
        operation=setup
        [[ $present != t ]] || operation=update
        temporal run --rm --no-deps schema "$operation" "$store"
    done
    touch /etc/sanctuary/temporal-schema-ready
fi
# Check real file readers before launching the supporting services.
temporal run --rm --no-deps --entrypoint /bin/sh temporal -ec 'test -r /certs/frontend.key && test -r /certs/internode.key'
temporal run --rm --no-deps --entrypoint /bin/sh ui -ec 'test -r /certs/ui-client.key'
temporal up -d --no-build temporal ui
for _attempt in {1..12}; do
    if temporal run --rm --no-deps bootstrap; then break; fi
    sleep 5
done
temporal run --rm --no-deps bootstrap
# Migration and application startup are shared with routine Boxie deployment.
bash "$owner/deploy.sh" "$revision"
if [[ ! -d /etc/sanctuary/grafana-plugins ]]; then
    plugin=$(mktemp -d)
    trap 'rm -rf "$plugin"' EXIT
    mapfile -t pins < <(python3 -c 'import json,sys; p=json.load(open(sys.argv[1]))["plugins"]["victoriametrics-logs-datasource"]; print(p["url"]); print(p["sha256"])' "$owner/observability/versions.json")
    curl --fail --silent --show-error --location --max-time 120 "${pins[0]}" -o "$plugin/plugin.zip"
    printf '%s  %s\n' "${pins[1]}" "$plugin/plugin.zip" | sha256sum --check --status
    mkdir "$plugin/unpacked"
    unzip -q "$plugin/plugin.zip" -d "$plugin/unpacked"
    mv "$plugin/unpacked" /etc/sanctuary/grafana-plugins
    chmod -R a+rX /etc/sanctuary/grafana-plugins
fi
timeout 180 docker compose --env-file /etc/sanctuary/boxie.env -f "$owner/observability/compose.yml" up -d --no-build --wait --wait-timeout 150
timeout 180 docker compose --env-file /etc/sanctuary/boxie.env -f "$owner/observability/compose.yml" up -d --no-build --no-deps --force-recreate --wait --wait-timeout 150 prometheus
timeout 180 docker compose --env-file /etc/sanctuary/boxie.env -f "$owner/ingress/compose.yml" run --rm cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
timeout 180 docker compose --env-file /etc/sanctuary/boxie.env -f "$owner/ingress/compose.yml" up -d --no-build --force-recreate cloudflared
