#!/usr/bin/env bash
# Invoked by deploy.py under the deployment lock; stdin has already been unpacked.
set -euo pipefail

staging=$1
release=$2
config=$3
source_dir="$staging/source"
app_env="$staging/app.env"
export PLAYGROUND_DNS_ENV_FILE="$staging/dns.env"

test "$(docker info --format '{{if index .Runtimes "runsc"}}runsc{{end}}')" = runsc

# This file is generated from the validated digest-only release manifest.
set -a
source "$source_dir/image.env"
set +a

compose() {
    docker compose --project-name cli-playground \
        --env-file "$app_env" --env-file "$source_dir/image.env" \
        --file "$source_dir/tools/cli-playground/compose.production.yml" "$@"
}

# Resolve configuration and pull every image before changing the running release.
compose config --quiet
docker pull --platform linux/amd64 "$PLAYGROUND_GATEWAY_IMAGE"
docker pull --platform linux/amd64 "$PLAYGROUND_SANDBOX_IMAGE"
compose pull proxy

if [ ! -d "$release" ]; then mv "$source_dir" "$release"; fi
install -m 0600 "$staging/app.env" "$config/app.env"
install -m 0600 "$staging/dns.env" "$config/dns.env"
source_dir=$release
app_env="$config/app.env"
export PLAYGROUND_DNS_ENV_FILE="$config/dns.env"
compose up --detach --no-build --pull never --wait --wait-timeout 180
