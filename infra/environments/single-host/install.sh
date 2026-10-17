#!/usr/bin/env bash
# Root-owned checkout, settings and tunnel credential files are installed by setup.
set -euo pipefail
[[ $# == 1 && $1 == /opt/clis/releases/setup/* ]] || { echo 'Use a prepared setup checkout.' >&2; exit 2; }
release=$1
/usr/local/sbin/clis-storage-guard
set -a
# shellcheck disable=SC1091
source /etc/clis/settings.env
# shellcheck disable=SC1091
source "$release/image.env"
set +a
: "${BOXIE_RUNTIME_IMAGE:?}" "${PLAYGROUND_IMAGE:?}" "${PLAYGROUND_SANDBOX_IMAGE:?}" "${OBS_COLLECTOR_IMAGE:?}"
for image in "$BOXIE_RUNTIME_IMAGE" "$PLAYGROUND_IMAGE" "$PLAYGROUND_SANDBOX_IMAGE" "$OBS_COLLECTOR_IMAGE"; do
    [[ $image =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || { echo 'Use registry-qualified image digests.' >&2; exit 2; }
    timeout 180 docker pull "$image" >/dev/null
done
[[ $(docker info --format '{{if index .Runtimes "runsc"}}runsc{{end}}') == runsc ]]
run=$(basename "$release")
for app in boxie playground; do
    target="/opt/clis/releases/$app/$run"
    if [[ ! -d $target ]]; then
        install -d -m 0700 "$(dirname "$target")"
        package=boxie
        [[ $app != playground ]] || package=cli-playground
        install -d -m 0700 "$target/tools/$package"
        cp -a "$release/tools/$package/infra" "$target/tools/$package/infra"
        cp "$release/image.env" "$release/revision" "$target/"
    fi
done
bash "$release/tools/boxie/infra/credentials.sh" /etc/clis/boxie
python3 "$release/tools/cli-playground/infra/playground_infra/installation.py" "$release"
bash "/opt/clis/releases/boxie/$run/tools/boxie/infra/install.sh" "/opt/clis/releases/boxie/$run"
bash "/opt/clis/releases/playground/$run/tools/cli-playground/infra/install.sh" "/opt/clis/releases/playground/$run"
bash "/opt/clis/releases/playground/$run/tools/cli-playground/infra/deploy.sh" "/opt/clis/releases/playground/$run"
