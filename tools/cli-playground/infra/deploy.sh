#!/usr/bin/env bash
# Local gateway operation; the systemd entry point holds the host lock.
set -euo pipefail
[[ $# == 1 ]] || exit 2
release=$1
stage() { if [[ -n ${CLIS_STAGE_FILE:-} ]]; then printf '%s\n' "$1" > "$CLIS_STAGE_FILE"; fi; }
config=${CLIS_CONFIG_DIR:-/etc/clis}
revision=$(cat "$release/revision")
[[ $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
# Every success record carries the effective gateway configuration, including
# sandbox-only releases that leave the running gateway untouched.
if [[ ! -e $release/playground.env ]]; then
    snapshot="$config/playground.env"
    if [[ ! -f $release/gateway.changed && ! -f $release/recovery ]]; then
        snapshot="${CLIS_STATE_DIR:-/srv/clis/deployment}/playground/current/playground.env"
    fi
    install -m 0600 "$snapshot" "$release/playground.env"
fi
if [[ -f $release/sandbox.changed || -f $release/recovery ]]; then
    current="${CLIS_STATE_DIR:-/srv/clis/deployment}/boxie/current"
    [[ $(cat "$current/revision") == "$(cat "$release/expected-boxie-revision")" ]] || { echo 'Boxie changed since preflight.' >&2; exit 1; }
    : "${PLAYGROUND_SANDBOX_IMAGE:?}"
    stage pulling-sandbox
    docker pull "$PLAYGROUND_SANDBOX_IMAGE"
    cp "$config/boxie/profiles.json" "$release/profiles.before.json"
    python3 "$release/tools/cli-playground/infra/playground_infra/installation.py" "$release" --profile-only
    BOXIE_RUNTIME_IMAGE=$(sed -n 's/^BOXIE_RUNTIME_IMAGE=//p' "$current/image.env")
    [[ $BOXIE_RUNTIME_IMAGE =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]]
    export BOXIE_RUNTIME_IMAGE
    bash "$current/tools/boxie/infra/deploy.sh" "$(readlink -f "$current")"
fi
[[ -f $release/gateway.changed || -f $release/recovery ]] || exit 0
compose() {
    docker compose --env-file "$release/playground.env" --env-file "$release/image.env" \
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
import time
from urllib.error import URLError
from pathlib import Path
from urllib.request import Request, urlopen

credential = Path(os.environ['PLAYGROUND_BOXIE_CREDENTIAL_FILE']).read_text().strip()
request = Request(os.environ['PLAYGROUND_BOXIE_URL'].rstrip('/') + '/readyz',
                  headers={'Authorization': 'Bearer ' + credential})
deadline = time.monotonic() + 300
while True:
    stage = 'authenticated Boxie readiness'
    try:
        with urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise ValueError('unexpected status')
        stage = 'public HTTPS readiness'
        with urlopen(os.environ['PLAYGROUND_PUBLIC_URL'].rstrip('/') + '/healthz', timeout=15) as response:
            if response.status != 200:
                raise ValueError('unexpected status')
        break
    except (URLError, OSError, ValueError) as error:
        if time.monotonic() >= deadline:
            raise SystemExit(f'{stage} failed after 300 seconds: {error}') from error
        time.sleep(5)
PY
