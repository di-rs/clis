#!/bin/bash
# First boot on a dedicated Ubuntu 24.04 x86_64 host. No cloud/product credentials.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
mount_path=/srv/boxie
expected_device=$(cat /etc/boxie-platform/storage-device)
# The attachment follows server creation. Bound that first-boot wait explicitly.
for attempt in $(seq 1 120); do
  test ! -e "$expected_device" || break
  sleep 1
done
test -e "$expected_device"
install -d -m 0700 "$mount_path"
systemctl daemon-reload
systemctl enable --now srv-boxie.mount
/usr/local/sbin/boxie-storage-guard "$mount_path" "$expected_device"
# Every persistent byte, including Docker named volumes, lives on the attached disk.
install -d -m 0700 "$mount_path/docker" "$mount_path/volumes"
install -d -m 0755 /opt/clis
install -d -m 0700 /etc/boxie-platform/secrets
test "$(dpkg --print-architecture)" = amd64
apt-get update
apt-get install -y acl ca-certificates curl gnupg python3 unattended-upgrades
/usr/local/sbin/boxie-journal-setup
install -m 0755 -d /etc/apt/keyrings
curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'SOURCES'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
curl -fsSL --retry 3 https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main' > /etc/apt/sources.list.d/gvisor.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin runsc
# systrap works in a VPS without nested virtualization or /dev/kvm.
runsc install -- --platform=systrap --fdlimit=4096
systemctl daemon-reload
systemctl enable docker
systemctl restart docker
docker run --rm --runtime=runsc hello-world
sshd -t
systemctl try-reload-or-restart ssh.service
