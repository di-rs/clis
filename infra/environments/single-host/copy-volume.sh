#!/usr/bin/env bash
# Run on the daemon host after stopping every writer; keep the old volume for rollback.
set -euo pipefail
[[ $# == 2 && $1 != "$2" ]] || exit 2
source=$1
target=$2
[[ $source =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ && $target =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || exit 2
[[ $(id -u) == 0 ]] || { echo 'Run on the Docker host as root.' >&2; exit 1; }
command -v rsync >/dev/null
# Mountpoint paths must refer to this host, never a remote Docker context.
unset DOCKER_CONTEXT
export DOCKER_HOST=unix:///var/run/docker.sock
[[ -z $(docker ps -q --filter "volume=$source") ]] || { echo 'Source volume still has running consumers.' >&2; exit 1; }
[[ $(docker volume inspect --format '{{.Driver}}' "$source") == local ]]
options=$(docker volume inspect --format '{{json .Options}}' "$source")
[[ $options == null || $options == '{}' ]]
if docker volume inspect "$target" >/dev/null 2>&1; then
    echo 'Target volume already exists; inspect and remove only the failed copy before retrying.' >&2
    exit 1
fi
old=$(docker volume inspect --format '{{.Mountpoint}}' "$source")
[[ -d $old && ! -L $old ]]
docker volume create "$target" >/dev/null
new=$(docker volume inspect --format '{{.Mountpoint}}' "$target")
[[ -d $new && ! -L $new ]]
rsync -aHAX --numeric-ids "$old/" "$new/"
changes=$(rsync -aHAXnc --numeric-ids --delete --itemize-changes "$old/" "$new/")
[[ -z $changes ]] || { echo 'Volume verification failed; keep traffic stopped.' >&2; exit 1; }
printf 'Verified volume copy: %s -> %s; original retained.\n' "$source" "$target"
