#!/usr/bin/env bash
# Change the approved sandbox profile and reload only its orchestrator.
set -euo pipefail
[[ $# == 1 && $1 =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
image=$1
config=${SANCTUARY_CONFIG_DIR:-/etc/sanctuary}
playground=${SANCTUARY_PLAYGROUND_DIR:-/opt/sanctuary/playground}
boxie=${SANCTUARY_BOXIE_DIR:-/opt/sanctuary/boxie}
docker pull "$image"
PLAYGROUND_SANDBOX_IMAGE=$image python3 "$playground/cli_playground_infra/installation.py" "$playground" --profile-only
compose() {
    docker compose --env-file "$config/boxie.env" --env-file "$config/boxie-image.env" \
        -f "$boxie/compose.production.yml" -f "$boxie/compose.observability.yml" "$@"
}
compose stop --timeout 60 orchestrator
compose up -d --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 150 orchestrator
compose exec -T api python - <<'PY'
import json
import time
import urllib.request
from pathlib import Path

request = urllib.request.Request('http://127.0.0.1:8090/readyz', headers={
    'Authorization': 'Bearer ' + Path('/run/secrets/consumer').read_text().strip(),
})
for attempt in range(30):
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            data = json.load(response)
        if response.status == 200 and data['dependencies']['orchestration'] is True:
            break
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(2)
else:
    raise SystemExit('Boxie orchestration did not become ready')
PY
