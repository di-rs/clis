#!/usr/bin/env bash
# Local entry point for the three fixed systemd operations. The unit owns flock.
set -euo pipefail
umask 077
[[ $# == 2 && $2 =~ ^[0-9]+-[0-9]+$ ]] || exit 2
app=$1 run=$2
case $app in
    setup) script=infra/environments/single-host/install.sh ;;
    boxie) script=tools/boxie/infra/deploy.sh ;;
    playground) script=tools/cli-playground/infra/deploy.sh ;;
    *) exit 2 ;;
esac
release="${CLIS_RELEASE_ROOT:-/opt/clis/releases}/$app/$run"
state=${CLIS_STATE_DIR:-/srv/clis/deployment}
if [[ -f $release/registry/config.json ]]; then export DOCKER_CONFIG="$release/registry"; fi
revision=$(cat "$release/revision")
[[ $revision =~ ^[a-f0-9]{40}$ ]] || exit 2
# Nonsecret assignments are data, never evaluated as shell commands.
while IFS='=' read -r name value; do
    case $name in BOXIE_RUNTIME_IMAGE|PLAYGROUND_IMAGE|PLAYGROUND_SANDBOX_IMAGE|OBS_COLLECTOR_IMAGE) ;; *) exit 2 ;; esac
    [[ $value =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
    export "$name=$value"
done < "$release/image.env"
mkdir -p "$state/$app"
[[ ! -e $state/pending ]] || { echo 'Previous operation requires inspection: see deployment/pending and journalctl.' >&2; exit 1; }
if [[ $app != setup ]]; then
    current=none
    [[ ! -e $state/$app/current/revision ]] || current=$(cat "$state/$app/current/revision")
    [[ $(cat "$release/expected-revision") == "$current" ]] || { echo 'Deployment changed since workflow preflight; inspect and retry.' >&2; exit 1; }
fi
bash "$release/$script" preflight "$release"
printf '%s %s %s\n' "$app" "$run" "$revision" > "$state/pending"
python3 - "$state/pending" "$state" <<'PY'
import os
import sys
for path in sys.argv[1:]:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY
# A failed process or timeout deliberately keeps the pending marker.
bash "$release/$script" apply "$release"
ln -s "$release" "$state/$app/current-$run"
python3 - "$state/$app/current-$run" "$state/$app/current" <<'PY'
import os
import sys
os.replace(sys.argv[1], sys.argv[2])
descriptor = os.open(os.path.dirname(sys.argv[2]), os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
rm "$state/pending"
