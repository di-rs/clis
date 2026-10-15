"""Small, validated Hetzner host definition, independent of application secrets."""

import base64
import binascii
import ipaddress
import json
import re
from pathlib import Path
from typing import Literal

import pulumi
import pulumi_cloudflare as cloudflare
import pulumi_hcloud as hcloud
import pulumi_tls as tls
from pydantic import BaseModel, ConfigDict, Field, field_validator


class HostConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    domain: str
    cloudflare_zone_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    ssh_public_key: str | None = None
    ssh_cidrs: list[str] = Field(default_factory=lambda: ["0.0.0.0/0"])
    location: Literal["nbg1", "fsn1"] = "nbg1"
    server_type: str = Field(default="cx33", pattern=r"^(?:cx|cpx|ccx)[0-9]+$")
    backups: bool = False
    protect: bool = True

    @field_validator("domain")
    @classmethod
    def dns_hostname(cls, value: str) -> str:
        labels = value.split(".")
        if (
            len(value) > 220
            or len(labels) < 2
            or not re.fullmatch(r"[a-z]{2,63}", labels[-1])
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
            )
        ):
            raise ValueError("domain must be a lowercase DNS hostname, e.g. play.example.com")
        return value

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


def cloud_init(host_private_key: str, host_public_key: str) -> str:
    """Install a pinned SSH host identity; no provider or application tokens are used."""
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
                    "path": "/usr/local/sbin/cli-playground-bootstrap.sh",
                    "permissions": "0700",
                    "content": Path(__file__).with_name("bootstrap.sh").read_text(),
                },
                {
                    "path": "/etc/ssh/sshd_config.d/00-cli-playground.conf",
                    "permissions": "0644",
                    "content": "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n",
                },
            ],
            "runcmd": [["bash", "/usr/local/sbin/cli-playground-bootstrap.sh"]],
        }
    )


def provision(config: HostConfig) -> hcloud.Server:
    labels = {"service": "cli-playground", "managed-by": "pulumi"}
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
        "playground",
        labels=labels,
        rules=rules,
    )
    ipv4 = hcloud.PrimaryIp(
        "playground-ipv4",
        type="ipv4",
        assignee_type="server",
        location=config.location,
        auto_delete=False,
        delete_protection=config.protect,
        labels=labels,
        opts=pulumi.ResourceOptions(protect=config.protect),
    )
    user_data: pulumi.Output[str] = pulumi.Output.secret(
        pulumi.Output.all(host_key.private_key_openssh, host_public_key).apply(
            lambda keys: cloud_init(keys[0], keys[1])
        )
    )
    server = hcloud.Server(
        "playground",
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
    cloudflare.DnsRecord(
        "playground",
        zone_id=config.cloudflare_zone_id,
        name=config.domain,
        type="A",
        content=ipv4.ip_address,
        ttl=300,
        proxied=False,
    )
    cloudflare.DnsRecord(
        "playground-previews",
        zone_id=config.cloudflare_zone_id,
        name=f"*.{config.domain}",
        type="A",
        content=ipv4.ip_address,
        ttl=300,
        proxied=False,
    )
    pulumi.export("ipv4", ipv4.ip_address)
    pulumi.export("sshPrivateKey", pulumi.Output.secret(deployment_key.private_key_openssh))
    pulumi.export("sshHostPublicKey", host_public_key)
    pulumi.export("publicUrl", f"https://{config.domain}")
    return server
