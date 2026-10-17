#!/usr/bin/env bash
# Install committed Compose files and private settings after cloud-init.
set -euo pipefail
[[ $# == 1 && -d $1 ]] || exit 2
source=$1
/usr/local/sbin/clis-storage-guard
install -d -m 0700 /etc/clis
for file in settings.env tunnel.json boxie-image.env playground-image.env; do
    install -m 0600 "$source/$file" "/etc/clis/$file"
done
install -d -m 0755 /opt/clis/boxie /opt/clis/playground
cp -a "$source/tools/boxie/infra/." /opt/clis/boxie/
cp -a "$source/tools/cli-playground/infra/." /opt/clis/playground/
set -a
# shellcheck disable=SC1091
source /etc/clis/settings.env
# shellcheck disable=SC1091
source /etc/clis/boxie-image.env
# shellcheck disable=SC1091
source /etc/clis/playground-image.env
set +a
[[ $(docker info --format '{{if index .Runtimes "runsc"}}runsc{{end}}') == runsc ]]
for image in "$BOXIE_RUNTIME_IMAGE" "$PLAYGROUND_IMAGE" "$PLAYGROUND_SANDBOX_IMAGE" "$OBS_COLLECTOR_IMAGE"; do
    [[ $image =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
    timeout 180 docker pull "$image" >/dev/null
done
bash /opt/clis/boxie/credentials.sh /etc/clis/boxie
python3 /opt/clis/playground/playground_infra/installation.py /opt/clis/playground
bash /opt/clis/boxie/install.sh "$source" "$(cat "$source/revision")"
bash /opt/clis/playground/install.sh
bash /opt/clis/playground/deploy.sh
