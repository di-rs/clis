#!/bin/bash
# First boot only, on a dedicated Ubuntu 24.04 x86_64 server. No secrets here.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
test "$(dpkg --print-architecture)" = amd64
apt-get update
apt-get install -y ca-certificates curl gnupg python3 unattended-upgrades unzip
# Prevent package post-install from starting either daemon on the root filesystem.
systemctl mask docker.service docker.socket containerd.service
install -m 0755 -d /etc/apt/keyrings
curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
curl -fsSL --retry 3 https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main' > /etc/apt/sources.list.d/gvisor.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin runsc
# The signed package installs runsc AND its companion binaries.
# systrap works inside a VPS and needs no nested virtualization or /dev/kvm.
runsc install -- --platform=systrap --fdlimit=4096
device=$(cat /etc/clis/storage-device)
for _attempt in {1..120}; do
    [[ -b $device ]] && break
    sleep 2
done
[[ -b $device ]] || { echo 'Persistent volume attachment did not appear.' >&2; exit 1; }
install -d -m 0755 /srv/clis
if findmnt --mountpoint /srv/clis >/dev/null; then
    /usr/local/sbin/clis-storage-guard
else
    # Only an entirely blank disk may be formatted. Recognized CLIS_DATA is reusable.
    if [[ -z $(wipefs --noheadings --output TYPE "$device") ]]; then
        [[ $(lsblk --noheadings --output TYPE "$device" | wc -l) -eq 1 ]]
        mkfs.ext4 -L CLIS_DATA "$device"
    else
        [[ $(blkid -s TYPE -o value "$device") == ext4 && $(blkid -s LABEL -o value "$device") == CLIS_DATA ]] || {
            echo 'Unknown nonempty volume; refusing to format.' >&2; exit 1;
        }
    fi
    uuid=$(blkid -s UUID -o value "$device")
    if ! grep -q "^UUID=$uuid /srv/clis " /etc/fstab; then
        if grep -Eq '[[:space:]]/srv/clis[[:space:]]' /etc/fstab; then
            echo 'Conflicting persistent mount in fstab.' >&2; exit 1
        fi
        printf 'UUID=%s /srv/clis ext4 defaults 0 2\n' "$uuid" >> /etc/fstab
    fi
    mount /srv/clis
fi
/usr/local/sbin/clis-storage-guard
install -d -m 0700 /etc/clis /srv/clis/docker /srv/clis/containerd
install -d -m 0755 /opt/clis/boxie /opt/clis/playground /srv/clis/boxie /srv/clis/playground
install -d -m 0755 /etc/docker /etc/containerd \
    /etc/systemd/system/docker.service.d /etc/systemd/system/containerd.service.d
python3 - <<'PYTHON'
import json
from pathlib import Path
p = Path('/etc/docker/daemon.json')
config = json.loads(p.read_text()) if p.exists() else {}
if config.get('data-root', '/srv/clis/docker') != '/srv/clis/docker':
    raise SystemExit('Conflicting Docker data root; operator repair required')
config['data-root'] = '/srv/clis/docker'
p.write_text(json.dumps(config))
PYTHON
# Docker 29+ stores image contents and snapshots in the system containerd daemon.
cat > /etc/containerd/config.toml <<'EOF'
version = 2
root = "/srv/clis/containerd"
disabled_plugins = ["cri"]
EOF
for service in docker containerd; do
    cat > "/etc/systemd/system/$service.service.d/storage.conf" <<'EOF'
[Unit]
RequiresMountsFor=/srv/clis
After=srv-clis.mount
BindsTo=srv-clis.mount
[Service]
ExecStartPre=/usr/local/sbin/clis-storage-guard
EOF
done
install -d /etc/systemd/journald.conf.d
install -m 0644 /dev/stdin /etc/systemd/journald.conf.d/clis.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=1G
EOF
systemctl daemon-reload
systemctl unmask docker.service docker.socket containerd.service
systemctl enable containerd docker
systemctl restart containerd
systemctl restart docker
systemctl restart systemd-journald
docker run --rm --runtime=runsc hello-world
sshd -t
systemctl try-reload-or-restart ssh.service
