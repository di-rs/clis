#!/bin/bash
# Persistent bounded journal; grant collector group read/traversal, never write.
set -euo pipefail
root=${1:-}
journal="$root/var/log/journal"
test -s "$root/etc/systemd/journald.conf.d/60-clis.conf"
entry=$(getent group systemd-journal)
IFS=: read -r group_name _ group_gid _ <<< "$entry"
test "$group_name" = systemd-journal
[[ "$group_gid" =~ ^[1-9][0-9]*$ ]]
machine_id=$(cat "$root/etc/machine-id")
[[ "$machine_id" =~ ^[0-9a-f]{32}$ ]]
timeout 60 systemd-tmpfiles --create --prefix=/var/log/journal
install -d -o root -g systemd-journal -m 2750 "$journal" "$journal/$machine_id"
# New boot/machine directories and files inherit the reader group ACL.
timeout 60 setfacl -m g:systemd-journal:rX,d:g:systemd-journal:rX "$journal" "$journal/$machine_id"
chmod 0644 "$root/etc/machine-id"
timeout 60 systemctl restart systemd-journald.service
timeout 60 journalctl --flush
timeout 60 setfacl -R -m g:systemd-journal:rX "$journal"
# Public host-discovered handle, consumed as OBS_JOURNAL_GROUP_GID at rendering.
install -d -o root -g root -m 0755 "$root/etc/boxie-platform"
printf '%s\n' "$group_gid" > "$root/etc/boxie-platform/journal-group-gid"
chmod 0644 "$root/etc/boxie-platform/journal-group-gid"
