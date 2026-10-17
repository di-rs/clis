"""Small, validated Hetzner host definition, independent of application secrets."""

import base64
import binascii
import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pulumi
import pulumi_hcloud as hcloud
import pulumi_tls as tls
from pulumi_command import remote
from pydantic import BaseModel, ConfigDict, Field, field_validator


class HostConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    disk_size: int = Field(default=80, ge=20, le=1024)
    ssh_public_key: str | None = None
    ssh_cidrs: list[str] = Field(default_factory=lambda: ["0.0.0.0/0"])
    location: Literal["nbg1", "fsn1"] = "nbg1"
    server_type: str = Field(default="cx33", pattern=r"^(?:cx|cpx|ccx)[0-9]+$")
    backups: bool = False
    protect: bool = True

    @field_validator("ssh_cidrs")
    @classmethod
    def ssh_networks(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("sshCidrs must include at least one IPv4 network")
        for cidr in value:
            try:
                network = ipaddress.ip_network(cidr, strict=True)
                if "/" not in cidr or network.version != 4:
                    raise ValueError("expected an explicit IPv4 network")
            except ValueError as error:
                raise ValueError("sshCidrs must contain explicit IPv4 networks") from error
        return value

    @field_validator("ssh_public_key")
    @classmethod
    def openssh_public_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split()
        if (
            any(character in value for character in "\n\r")
            or len(parts) < 2
            or parts[0]
            not in {
                "ssh-ed25519",
                "ssh-rsa",
                "ecdsa-sha2-nistp256",
                "ecdsa-sha2-nistp384",
                "ecdsa-sha2-nistp521",
            }
        ):
            raise ValueError("sshPublicKey must be an OpenSSH public key, without options")
        try:
            base64.b64decode(parts[1], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("sshPublicKey contains invalid base64") from error
        return value


def cloud_init(host_private_key: str, host_public_key: str, volume_id: str) -> str:
    """Install a pinned SSH host identity; no provider or application tokens are used."""
    source = Path(__file__).resolve().parent.parent
    if not (source / "bootstrap.sh").is_file():
        raise RuntimeError("Provisioning requires the infrastructure source checkout")
    return "#cloud-config\n" + json.dumps(
        {
            "ssh_pwauth": False,
            "disable_root": False,
            "ssh_keys": {
                "ed25519_private": host_private_key,
                "ed25519_public": host_public_key,
            },
            "write_files": [
                {
                    "path": "/usr/local/sbin/clis-operation",
                    "permissions": "0700",
                    "content": (source / "operation.sh").read_text(),
                },
                *[
                    {
                        "path": f"/etc/systemd/system/{unit.name}",
                        "permissions": "0644",
                        "content": unit.read_text(),
                    }
                    for unit in sorted((source / "systemd").glob("*.service"))
                ],
                {
                    "path": "/etc/clis/storage-device",
                    "permissions": "0600",
                    "content": f"/dev/disk/by-id/scsi-0HC_Volume_{volume_id}\n",
                },
                {
                    "path": "/usr/local/sbin/clis-storage-guard",
                    "permissions": "0700",
                    "content": (source / "storage-guard.sh").read_text(),
                },
                {
                    "path": "/usr/local/sbin/clis-bootstrap",
                    "permissions": "0700",
                    "content": (
                        Path(__file__).resolve().parent.parent / "bootstrap.sh"
                    ).read_text(),
                },
                {
                    "path": "/etc/ssh/sshd_config.d/00-clis.conf",
                    "permissions": "0644",
                    "content": "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n",
                },
            ],
            "runcmd": [["bash", "/usr/local/sbin/clis-bootstrap"]],
        }
    )


@dataclass(frozen=True)
class HostResources:
    server: hcloud.Server
    volume_attachment: hcloud.VolumeAttachment
    connection: remote.ConnectionArgs


def provision(config: HostConfig) -> hcloud.Server:
    return provision_resources(config).server


def provision_resources(config: HostConfig) -> HostResources:
    labels = {"service": "clis", "managed-by": "pulumi"}
    private_outputs = ["privateKeyOpenssh", "privateKeyPem", "privateKeyPemPkcs8"]
    deployment_key = tls.PrivateKey(
        "deployment-key",
        algorithm="ED25519",
        opts=pulumi.ResourceOptions(additional_secret_outputs=private_outputs),
    )
    host_key = tls.PrivateKey(
        "host-key",
        algorithm="ED25519",
        opts=pulumi.ResourceOptions(additional_secret_outputs=private_outputs),
    )
    host_public_key = host_key.public_key_openssh.apply(str.strip)
    deployment = hcloud.SshKey(
        "deployment",
        public_key=deployment_key.public_key_openssh.apply(str.strip),
        labels=labels,
    )
    ssh_keys = [deployment.id]
    if config.ssh_public_key:
        # Keep the existing resource name for stacks that already have admin access.
        admin = hcloud.SshKey("admin", public_key=config.ssh_public_key, labels=labels)
        ssh_keys.append(admin.id)

    rules = [
        hcloud.FirewallRuleArgs(
            direction="in", protocol="tcp", port=port, source_ips=["0.0.0.0/0", "::/0"]
        )
        for port in ("80", "443")
    ]
    rules.append(
        hcloud.FirewallRuleArgs(direction="in", protocol="icmp", source_ips=["0.0.0.0/0", "::/0"])
    )
    rules.insert(
        0,
        hcloud.FirewallRuleArgs(
            direction="in", protocol="tcp", port="22", source_ips=config.ssh_cidrs
        ),
    )
    firewall = hcloud.Firewall(
        "clis",
        labels=labels,
        rules=rules,
    )
    ipv4 = hcloud.PrimaryIp(
        "clis-ipv4",
        type="ipv4",
        assignee_type="server",
        location=config.location,
        auto_delete=False,
        delete_protection=config.protect,
        labels=labels,
        opts=pulumi.ResourceOptions(protect=config.protect),
    )
    volume = hcloud.Volume(
        "clis-data",
        size=config.disk_size,
        location=config.location,
        delete_protection=config.protect,
        labels=labels,
        opts=pulumi.ResourceOptions(protect=config.protect),
    )
    user_data: pulumi.Output[str] = pulumi.Output.secret(
        pulumi.Output.all(host_key.private_key_openssh, host_public_key, volume.id).apply(
            lambda keys: cloud_init(keys[0], keys[1], keys[2])
        )
    )
    server = hcloud.Server(
        "clis",
        server_type=config.server_type,
        location=config.location,
        image="ubuntu-24.04",
        backups=config.backups,
        delete_protection=config.protect,
        rebuild_protection=config.protect,
        shutdown_before_deletion=True,
        ssh_keys=ssh_keys,
        # Attach the firewall before the first boot, not in a later attachment.
        firewall_ids=[firewall.id.apply(int)],
        public_nets=[
            hcloud.ServerPublicNetArgs(
                ipv4=ipv4.id.apply(int), ipv4_enabled=True, ipv6_enabled=False
            )
        ],
        user_data=user_data,
        labels=labels,
        opts=pulumi.ResourceOptions(protect=config.protect, additional_secret_outputs=["userData"]),
    )
    attachment = hcloud.VolumeAttachment(
        "clis-data", volume_id=volume.id.apply(int), server_id=server.id.apply(int), automount=False
    )
    pulumi.export("ipv4", ipv4.ip_address)
    pulumi.export("sshPrivateKey", pulumi.Output.secret(deployment_key.private_key_openssh))
    pulumi.export("sshHostPublicKey", host_public_key)
    return HostResources(
        server,
        attachment,
        remote.ConnectionArgs(
            host=ipv4.ip_address,
            user="root",
            private_key=pulumi.Output.secret(deployment_key.private_key_openssh),
            host_key=host_public_key,
            per_dial_timeout=15,
            dial_error_limit=20,
        ),
    )
