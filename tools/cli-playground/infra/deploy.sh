#!/usr/bin/env bash
# Local gateway operation; the systemd entry point holds the host lock.
set -euo pipefail
[[ $# == 2 && $1 =~ ^(preflight|apply)$ ]] || exit 2
phase=$1 release=$2
config=${CLIS_CONFIG_DIR:-/etc/clis}
revision=$(cat "$release/revision")
[[ $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
# Every success record carries the effective gateway configuration, including
# sandbox-only releases that leave the running gateway untouched.
if [[ $phase == preflight && ! -e $release/playground.env ]]; then
    snapshot="$config/playground.env"
    if [[ ! -f $release/gateway.changed && ! -f $release/recovery ]]; then
        snapshot="${CLIS_STATE_DIR:-/srv/clis/deployment}/playground/current/playground.env"
    fi
    install -m 0600 "$snapshot" "$release/playground.env"
fi
[[ -f $release/playground.env ]] || exit 2
if [[ -f $release/sandbox.changed || -f $release/recovery ]]; then
    current="${CLIS_STATE_DIR:-/srv/clis/deployment}/boxie/current"
    [[ $(cat "$current/revision") == "$(cat "$release/expected-boxie-revision")" ]] || { echo 'Boxie changed since preflight.' >&2; exit 1; }
    : "${PLAYGROUND_SANDBOX_IMAGE:?}"
    if [[ $phase == preflight ]]; then
        docker pull "$PLAYGROUND_SANDBOX_IMAGE"
        cp "$config/boxie/profiles.json" "$release/profiles.before.json"
    else
        python3 "$release/tools/cli-playground/infra/playground_infra/installation.py" "$release" --profile-only
    fi
    BOXIE_RUNTIME_IMAGE=$(sed -n 's/^BOXIE_RUNTIME_IMAGE=//p' "$current/image.env")
    [[ $BOXIE_RUNTIME_IMAGE =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]]
    export BOXIE_RUNTIME_IMAGE
    if [[ $phase == preflight ]]; then
        bash "$current/tools/boxie/infra/deploy.sh" preflight "$(readlink -f "$current")"
    else
        bash "$current/tools/boxie/infra/deploy.sh" apply "$(readlink -f "$current")"
    fi
fi
[[ -f $release/gateway.changed || -f $release/recovery ]] || exit 0
compose() {
    docker compose --env-file "$release/playground.env" --env-file "$release/image.env" \
        -f "$release/tools/cli-playground/infra/compose.production.yml" "$@"
}
if [[ $phase == preflight ]]; then
    compose config --quiet
    compose pull gateway
    exit 0
fi
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 180 gateway
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
