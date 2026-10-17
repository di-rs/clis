#!/bin/bash
# Refuse host-root storage even if the intended mount directory already exists.
set -euo pipefail
mount_path=${1:?persistent mount path required}
expected_device=${2:?persistent device required}
actual_device=$(findmnt --noheadings --output SOURCE --mountpoint "$mount_path")
test -n "$actual_device"
test -e "$expected_device"
test "$(readlink -f "$actual_device")" = "$(readlink -f "$expected_device")"
