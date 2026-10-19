#!/usr/bin/env bash
set -euo pipefail
mountpoint=${1:-/srv/sanctuary}
device=${2:-$(cat /etc/sanctuary/storage-device)}
if [[ ! -b $device ]] || ! findmnt --mountpoint "$mountpoint" --source "$device" --types ext4 >/dev/null; then
    echo 'Required persistent disk is absent or mounted elsewhere; refusing workloads.' >&2
    exit 1
fi
