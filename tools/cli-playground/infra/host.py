"""Small, validated Hetzner host definition, independent of application secrets."""

import base64
import binascii
import ipaddress
import json
import re
from pathlib import Path
from typing import Literal

import pulumi
import pulumi_hcloud as hcloud
from pydantic import BaseModel, ConfigDict, Field, field_validator


class HostConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    domain: str
    ssh_public_key: str
    admin_cidrs: list[str]
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

    @field_validator("admin_cidrs")
    @classmethod
    def restricted_ssh_networks(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("adminCidrs must contain at least one administrative CIDR")
        has_ipv4 = False
        for cidr in value:
            try:
                network = ipaddress.ip_network(cidr, strict=True)
                if "/" not in cidr or network.prefixlen == 0:
                    raise ValueError("unrestricted SSH")
                has_ipv4 |= network.version == 4
            except ValueError as error:
                raise ValueError(
                    "adminCidrs must be explicit networks; /0 is not allowed"
                ) from error
        if not has_ipv4:
            raise ValueError("adminCidrs needs an IPv4 network: this host has no public IPv6")
        return value

    @field_validator("ssh_public_key")
    @classmethod
    def openssh_public_key(cls, value: str) -> str:
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


def cloud_init() -> str:
    """JSON is valid YAML; no templating, public input or secret reaches the script."""
    return "#cloud-config\n" + json.dumps(
        {
            "ssh_pwauth": False,
            "disable_root": False,
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
    key = hcloud.SshKey("admin", public_key=config.ssh_public_key, labels=labels)
    firewall = hcloud.Firewall(
        "playground",
        labels=labels,
        rules=[
            hcloud.FirewallRuleArgs(
                direction="in", protocol="tcp", port="22", source_ips=config.admin_cidrs
            ),
            hcloud.FirewallRuleArgs(
                direction="in", protocol="tcp", port="80", source_ips=["0.0.0.0/0", "::/0"]
            ),
            hcloud.FirewallRuleArgs(
                direction="in", protocol="tcp", port="443", source_ips=["0.0.0.0/0", "::/0"]
            ),
            hcloud.FirewallRuleArgs(
                direction="in", protocol="icmp", source_ips=["0.0.0.0/0", "::/0"]
            ),
        ],
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
    server = hcloud.Server(
        "playground",
        server_type=config.server_type,
        location=config.location,
        image="ubuntu-24.04",
        backups=config.backups,
        delete_protection=config.protect,
        rebuild_protection=config.protect,
        shutdown_before_deletion=True,
        ssh_keys=[key.id],
        # Attach the firewall before the first boot, not in a later attachment.
        firewall_ids=[firewall.id.apply(int)],
        public_nets=[
            hcloud.ServerPublicNetArgs(
                ipv4=ipv4.id.apply(int), ipv4_enabled=True, ipv6_enabled=False
            )
        ],
        user_data=cloud_init(),
        labels=labels,
        opts=pulumi.ResourceOptions(protect=config.protect),
    )
    pulumi.export("ipv4", ipv4.ip_address)
    pulumi.export("publicUrl", f"https://{config.domain}")
    pulumi.export(
        "dnsRecords",
        ipv4.ip_address.apply(
            lambda address: [
                {"type": "A", "name": config.domain, "value": address},
                {"type": "A", "name": f"*.{config.domain}", "value": address},
            ]
        ),
    )
    pulumi.export("ssh", ipv4.ip_address.apply(lambda address: f"ssh root@{address}"))
    return server
