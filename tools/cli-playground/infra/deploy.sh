#!/usr/bin/env bash
# Update the backend without recreating the public proxy.
set -euo pipefail
[[ $# == 0 ]] || exit 2
config=${SANCTUARY_CONFIG_DIR:-/etc/sanctuary}
owner=${SANCTUARY_CLI_PLAYGROUND_DIR:-/opt/sanctuary/cli-playground}
compose() {
    docker compose --env-file "$config/cli-playground.env" --env-file "$config/cli-playground-image.env" \
        -f "$owner/compose.production.yml" "$@"
}
compose config --quiet
compose pull backend
compose run --rm --no-deps backend python -m cli_playground.migrate --dsn-file /run/secrets/postgres-dsn
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 180 backend
if compose exec -T backend /app/.venv/bin/python - <<'PY'
import os
import time
from urllib.error import URLError
from pathlib import Path
from urllib.request import Request, urlopen

credential = Path(os.environ['CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE']).read_text().strip()
request = Request(os.environ['CLI_PLAYGROUND_BOXIE_URL'].rstrip('/') + '/readyz',
                  headers={'Authorization': 'Bearer ' + credential})
deadline = time.monotonic() + 300
while True:
    stage = 'authenticated Boxie readiness'
    try:
        with urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise ValueError('unexpected status')
        stage = 'public HTTPS readiness'
        with urlopen(os.environ['CLI_PLAYGROUND_PUBLIC_URL'].rstrip('/') + '/healthz', timeout=15) as response:
            if response.status != 200:
                raise ValueError('unexpected status')
        break
    except (URLError, OSError, ValueError) as error:
        if time.monotonic() >= deadline:
            raise SystemExit(f'{stage} failed after 300 seconds: {error}') from error
        print(f'Waiting for {stage}: {error}', flush=True)
        time.sleep(5)
PY
then
    :
else
    status=$?
    compose logs --tail 80 proxy >&2 || true
    exit "$status"
fi
