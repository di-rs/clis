#!/usr/bin/env bash
# Local gateway operation; the systemd entry point holds the host lock.
set -euo pipefail
[[ $# == 1 ]] || exit 2
release=$1
stage() { if [[ -n ${CLIS_STAGE_FILE:-} ]]; then printf '%s\n' "$1" > "$CLIS_STAGE_FILE"; fi; }
config=${CLIS_CONFIG_DIR:-/etc/clis}
revision=$(cat "$release/revision")
[[ $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
compose() {
    docker compose --env-file "$config/playground.env" --env-file "$release/image.env" \
        -f "$release/tools/cli-playground/infra/compose.production.yml" "$@"
}
stage validating
compose config --quiet
stage pulling
compose pull gateway
stage starting
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 180 gateway
stage readiness
compose exec -T gateway /app/.venv/bin/python - <<'PY'
import os
from pathlib import Path
from urllib.request import Request, urlopen

credential = Path(os.environ['PLAYGROUND_BOXIE_CREDENTIAL_FILE']).read_text().strip()
request = Request(os.environ['PLAYGROUND_BOXIE_URL'].rstrip('/') + '/readyz',
                  headers={'Authorization': 'Bearer ' + credential})
with urlopen(request, timeout=10) as response:
    assert response.status == 200
with urlopen(os.environ['PLAYGROUND_PUBLIC_URL'].rstrip('/') + '/healthz', timeout=15) as response:
    assert response.status == 200
PY
