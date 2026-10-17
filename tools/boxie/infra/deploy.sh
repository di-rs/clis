#!/usr/bin/env bash
# Update only Boxie's API and orchestrator; PostgreSQL and Temporal stay up.
set -euo pipefail
umask 077
[[ $# == 1 && $1 =~ ^[a-f0-9]{40}$ ]] || exit 2
revision=$1
config=${CLIS_CONFIG_DIR:-/etc/clis}
owner=${CLIS_BOXIE_DIR:-/opt/clis/boxie}
image_file=${CLIS_BOXIE_IMAGE_FILE:-$config/boxie-image.env}
export BOXIE_RELEASE=$revision BOXIE_API_CONFIG=$owner/api.json
compose() {
    docker compose --env-file "$config/boxie.env" --env-file "$image_file" \
        -f "$owner/compose.production.yml" -f "$owner/compose.operations.yml" \
        -f "$owner/compose.observability.yml" "$@"
}
python3 - "$config/boxie/api.json" "$BOXIE_API_CONFIG" "$revision" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path
configuration = json.loads(Path(sys.argv[1]).read_text())
configuration['release'] = sys.argv[3]
target = Path(sys.argv[2])
with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as output:
    temporary = Path(output.name)
    output.write(json.dumps(configuration) + '\n')
os.replace(temporary, target)
PY
compose config --quiet
compose pull api orchestrator migrate
compose stop --timeout 60 api
compose stop --timeout 60 orchestrator
installation=$(cat "$config/boxie/installation-id")
host=$(cat "$config/boxie/host-id")
remaining=$(docker ps -aq --filter "label=boxie.installation=$installation" --filter "label=boxie.host=$host")
[[ -z $remaining ]] || { echo 'Sandbox cleanup incomplete; inspect before retry.' >&2; exit 1; }
compose run --rm --no-deps migrate
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 150 orchestrator
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 150 api
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
