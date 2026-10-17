#!/usr/bin/env bash
# Runs locally under the systemd operation's host lock.
set -euo pipefail
[[ $# == 1 ]] || exit 2
release=$1
stage() { if [[ -n ${CLIS_STAGE_FILE:-} ]]; then printf '%s\n' "$1" > "$CLIS_STAGE_FILE"; fi; }
config=${CLIS_CONFIG_DIR:-/etc/clis}
owner="$release/tools/boxie/infra"
revision=$(cat "$release/revision")
[[ $revision =~ ^[a-f0-9]{40}$ ]] || exit 2
export BOXIE_RELEASE=$revision
umask 077
export BOXIE_API_CONFIG="$release/api.json"
export BOXIE_ORCHESTRATOR_CONFIG="$release/orchestrator.json"
[[ -e $release/boxie.env ]] || install -m 0600 "$config/boxie.env" "$release/boxie.env"
[[ -e $BOXIE_ORCHESTRATOR_CONFIG ]] || install -m 0600 "$config/boxie/orchestrator.json" "$BOXIE_ORCHESTRATOR_CONFIG"
if [[ ! -e $BOXIE_API_CONFIG ]]; then
python3 - "$config/boxie/api.json" "$BOXIE_API_CONFIG" "$revision" <<'PY'
import json
import sys
from pathlib import Path
configuration = json.loads(Path(sys.argv[1]).read_text())
configuration['release'] = sys.argv[3]
Path(sys.argv[2]).write_text(json.dumps(configuration) + '\n')
PY
fi
compose() { docker compose --env-file "$release/boxie.env" --env-file "$release/image.env" -f "$owner/compose.production.yml" -f "$owner/compose.operations.yml" "$@"; }
stage validating
compose config --quiet
stage pulling
compose pull api orchestrator migrate
# Stop admission first; graceful orchestrator shutdown deletes its owned sandboxes.
stage stopping
compose stop --timeout 60 api
compose stop --timeout 60 orchestrator
installation=$(cat "$config/boxie/installation-id")
host=$(cat "$config/boxie/host-id")
remaining=$(docker ps -aq --filter "label=boxie.installation=$installation" --filter "label=boxie.host=$host")
[[ -z $remaining ]] || { echo 'Sandbox cleanup incomplete; inspect before recovery.' >&2; exit 1; }
stage migrating
compose run --rm --no-deps migrate
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 150 orchestrator
# Give the embedded lifecycle workers time to reconcile durable capacity before admission.
compose run --rm --no-deps -T --entrypoint python migrate - "$installation" "$host" <<'PYCODE'
import sys
import time
from pathlib import Path
import psycopg
with psycopg.connect(Path('/run/secrets/postgres-dsn').read_text().strip(), autocommit=True) as connection:
    for attempt in range(60):
        count = connection.execute(
            "SELECT count(*) FROM capacity_slots c JOIN sandboxes s ON s.id=c.sandbox_id WHERE s.installation_id=%s AND s.host_id=%s",
            (sys.argv[1], sys.argv[2]),
        ).fetchone()[0]
        if count == 0:
            break
        time.sleep(1)
    else:
        raise SystemExit('Sandbox capacity reconciliation incomplete')
PYCODE
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 150 api
# Readiness is authenticated and includes PostgreSQL, Temporal and orchestrator checks.
stage readiness
compose exec -T api python - <<'PY'
import json
import os
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
        if data['release'] == os.environ['BOXIE_RELEASE'] and all(data['dependencies'].values()):
            break
    except OSError:
        pass
    time.sleep(2)
else:
    raise SystemExit('Boxie dependencies did not become ready')
PY
stage lifecycle
compose run --rm --no-deps smoke
