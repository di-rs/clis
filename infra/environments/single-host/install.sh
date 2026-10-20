#!/usr/bin/env bash
# Install committed Compose files and private settings after cloud-init.
set -euo pipefail
[[ $# == 1 && -d $1 ]] || exit 2
source=$1
/usr/local/sbin/sanctuary-storage-guard
install -d -m 0700 /etc/sanctuary
for file in settings.env tunnel.json boxie-image.env cli-playground-image.env; do
    install -m 0600 "$source/$file" "/etc/sanctuary/$file"
done
install -d -m 0755 /opt/sanctuary/boxie /opt/sanctuary/cli-playground
cp -a "$source/packages/boxie/infra/." /opt/sanctuary/boxie/
cp -a "$source/tools/cli-playground/infra/." /opt/sanctuary/cli-playground/
set -a
# shellcheck disable=SC1091
source /etc/sanctuary/settings.env
# shellcheck disable=SC1091
source /etc/sanctuary/boxie-image.env
# shellcheck disable=SC1091
source /etc/sanctuary/cli-playground-image.env
set +a
[[ $(docker info --format '{{if index .Runtimes "runsc"}}runsc{{end}}') == runsc ]]
for image in "$BOXIE_IMAGE" "$CLI_PLAYGROUND_IMAGE" "$BOXIE_SANDBOX_IMAGE" "$OBS_COLLECTOR_IMAGE"; do
    [[ $image =~ ^[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
    timeout 180 docker pull "$image" >/dev/null
done
bash /opt/sanctuary/boxie/credentials.sh /etc/sanctuary/boxie
python3 /opt/sanctuary/cli-playground/cli_playground_infra/installation.py
bash /opt/sanctuary/boxie/install.sh "$source" "$(cat "$source/revision")" cli-playground
bash /opt/sanctuary/cli-playground/install.sh
bash /opt/sanctuary/cli-playground/deploy.sh
