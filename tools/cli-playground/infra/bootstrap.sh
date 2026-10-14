#!/bin/bash
# First boot only, on a dedicated Ubuntu 24.04 x86_64 server. No secrets here.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
test "$(dpkg --print-architecture)" = amd64
apt-get update
apt-get install -y ca-certificates curl gnupg python3 unattended-upgrades
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
systemctl enable docker
systemctl restart docker
docker run --rm --runtime=runsc hello-world
install -d -m 0700 /etc/cli-playground /var/lib/cli-playground-proxy
install -d -m 0755 /opt/cli-playground/releases /var/lib/cli-playground
sshd -t
systemctl try-reload-or-restart ssh.service
