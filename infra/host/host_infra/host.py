"""Provision a protected host and persistent disk independently of product secrets."""

import base64
import binascii
import configparser
import ipaddress
import json
import re
from pathlib import Path
from typing import Literal
from uuid import UUID

import pulumi
import pulumi_hcloud as hcloud
import pulumi_tls as tls
from pydantic import BaseModel, ConfigDict, Field, field_validator


class HostConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    installation_id: UUID
    identity_ref: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    ssh_public_key: str | None = None
    ssh_cidrs: list[str] = Field(default_factory=lambda: ["0.0.0.0/0"])
    public_http: bool = False
    location: Literal["nbg1", "fsn1"] = "nbg1"
    server_type: str = Field(default="cx33", pattern=r"^(?:cx|cpx|ccx)[0-9]+$")
    volume_size_gb: int = Field(default=40, ge=20, le=10240)
    backups: bool = False
    protect: bool = True

    @field_validator("ssh_cidrs")
    @classmethod
    def ssh_networks(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("sshCidrs must include at least one IPv4 network")
        for cidr in value:
            network = ipaddress.ip_network(cidr, strict=True)
            if "/" not in cidr or network.version != 4:
                raise ValueError("sshCidrs must contain explicit IPv4 networks")
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
            raise ValueError("sshPublicKey must be an OpenSSH public key without options")
        try:
            base64.b64decode(parts[1], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("sshPublicKey contains invalid base64") from error
        return value


def _validate_journal_policy(value: str) -> None:
    policy = configparser.ConfigParser(interpolation=None)
    policy.read_string(value)
    limits = {
        "SystemMaxUse": r"[1-9][0-9]*(?:[KMGTPE])?",
        "RuntimeMaxUse": r"[1-9][0-9]*(?:[KMGTPE])?",
        "SystemKeepFree": r"[1-9][0-9]*(?:[KMGTPE])?",
        "MaxRetentionSec": r"[1-9][0-9]*(?:us|ms|s|min|h|day|week|month|year)?",
        "MaxFileSec": r"[1-9][0-9]*(?:us|ms|s|min|h|day|week|month|year)?",
    }
    if policy.get("Journal", "Storage", fallback="") != "persistent" or any(
        not re.fullmatch(pattern, policy.get("Journal", key, fallback=""))
        for key, pattern in limits.items()
    ):
        raise ValueError("explicit finite bounded journal policy required")


def cloud_init(
    host_private_key: str, host_public_key: str, storage_device: str, *, journal_policy: str
) -> str:
    """Configure the pinned host identity and fail-closed storage before Docker starts."""
    _validate_journal_policy(journal_policy)
    source = Path(__file__).resolve().parent.parent
    if not re.fullmatch(r"/dev/disk/by-id/scsi-0HC_Volume_[0-9]+", storage_device):
        raise ValueError("expected an explicit persistent volume device")
    files = {
        "/etc/systemd/journald.conf.d/60-clis.conf": ("0644", journal_policy),
        "/usr/local/sbin/boxie-journal-setup": ("0700", (source / "journal-setup.sh").read_text()),
        "/usr/local/sbin/boxie-platform-bootstrap": ("0700", (source / "bootstrap.sh").read_text()),
        "/usr/local/sbin/boxie-storage-guard": ("0700", (source / "storage-guard.sh").read_text()),
        "/etc/boxie-platform/storage-device": ("0644", storage_device + "\n"),
        "/etc/systemd/system/srv-boxie.mount": (
            "0644",
            "[Unit]\nDescription=Boxie persistent data\n[Mount]\n"
            f"What={storage_device}\nWhere=/srv/boxie\nType=ext4\nOptions=defaults\nTimeoutSec=120\n"
            "[Install]\nWantedBy=local-fs.target\n",
        ),
        "/etc/docker/daemon.json": ("0644", json.dumps({"data-root": "/srv/boxie/docker"})),
        "/etc/systemd/system/docker.service.d/storage.conf": (
            "0644",
            "[Unit]\nRequiresMountsFor=/srv/boxie\n[Service]\n"
            f"ExecStartPre=/usr/local/sbin/boxie-storage-guard /srv/boxie {storage_device}\n",
        ),
        "/etc/ssh/sshd_config.d/00-boxie-platform.conf": (
            "0644",
            "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n",
        ),
    }
    return "#cloud-config\n" + json.dumps(
        {
            "ssh_pwauth": False,
            "disable_root": False,
            "ssh_keys": {"ed25519_private": host_private_key, "ed25519_public": host_public_key},
            "write_files": [
                {"path": path, "permissions": mode, "content": content}
                for path, (mode, content) in files.items()
            ],
            "runcmd": [["bash", "/usr/local/sbin/boxie-platform-bootstrap"]],
        }
    )


class Host(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: HostConfig,
        *,
        journal_policy: str,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        _validate_journal_policy(journal_policy)
        super().__init__("clis:platform:Host", name, None, opts)
        labels = {"installation": str(config.installation_id), "managed-by": "pulumi"}
        child = pulumi.ResourceOptions(parent=self)
        protected = pulumi.ResourceOptions(parent=self, protect=config.protect)
        private = pulumi.ResourceOptions(
            parent=self,
            additional_secret_outputs=["privateKeyOpenssh", "privateKeyPem", "privateKeyPemPkcs8"],
        )
        deployment_key = tls.PrivateKey(f"{name}-deployment-key", algorithm="ED25519", opts=private)
        host_key = tls.PrivateKey(f"{name}-host-key", algorithm="ED25519", opts=private)
        host_public_key = pulumi.Output.unsecret(host_key.public_key_openssh.apply(str.strip))
        deployment = hcloud.SshKey(
            f"{name}-deployment",
            public_key=deployment_key.public_key_openssh.apply(str.strip),
            labels=labels,
            opts=child,
        )
        ssh_keys = [deployment.id]
        if config.ssh_public_key:
            admin = hcloud.SshKey(
                f"{name}-admin", public_key=config.ssh_public_key, labels=labels, opts=child
            )
            ssh_keys.append(admin.id)
        rules = [
            hcloud.FirewallRuleArgs(
                direction="in", protocol="tcp", port="22", source_ips=config.ssh_cidrs
            ),
            hcloud.FirewallRuleArgs(direction="in", protocol="icmp", source_ips=["0.0.0.0/0"]),
        ]
        if config.public_http:
            rules.extend(
                hcloud.FirewallRuleArgs(
                    direction="in", protocol="tcp", port=port, source_ips=["0.0.0.0/0"]
                )
                for port in ("80", "443")
            )
        firewall = hcloud.Firewall(f"{name}-firewall", labels=labels, rules=rules, opts=child)
        ipv4 = hcloud.PrimaryIp(
            f"{name}-ipv4",
            type="ipv4",
            assignee_type="server",
            location=config.location,
            auto_delete=False,
            delete_protection=config.protect,
            labels=labels,
            opts=protected,
        )
        volume = hcloud.Volume(
            f"{name}-data",
            size=config.volume_size_gb,
            location=config.location,
            format="ext4",
            automount=False,
            delete_protection=config.protect,
            labels=labels,
            opts=protected,
        )
        self.storage_device = volume.id.apply(
            lambda value: f"/dev/disk/by-id/scsi-0HC_Volume_{value}"
        )
        user_data: pulumi.Output[str] = pulumi.Output.secret(
            pulumi.Output.all(
                host_key.private_key_openssh, host_public_key, self.storage_device
            ).apply(
                lambda values: cloud_init(
                    values[0], values[1], values[2], journal_policy=journal_policy
                )
            )
        )
        self.server = hcloud.Server(
            name,
            server_type=config.server_type,
            location=config.location,
            image="ubuntu-24.04",
            backups=config.backups,
            delete_protection=config.protect,
            rebuild_protection=config.protect,
            shutdown_before_deletion=True,
            ssh_keys=ssh_keys,
            firewall_ids=[firewall.id.apply(int)],
            public_nets=[
                hcloud.ServerPublicNetArgs(
                    ipv4=ipv4.id.apply(int), ipv4_enabled=True, ipv6_enabled=False
                )
            ],
            user_data=user_data,
            labels=labels,
            opts=pulumi.ResourceOptions(
                parent=self, protect=config.protect, additional_secret_outputs=["userData"]
            ),
        )
        self.volume_attachment = hcloud.VolumeAttachment(
            f"{name}-data",
            volume_id=volume.id.apply(int),
            server_id=self.server.id.apply(int),
            automount=False,
            opts=protected,
        )
        self.address = ipv4.ip_address
        self.connection_profile = pulumi.Output.all(
            host=self.address, host_public_key=host_public_key
        ).apply(lambda values: {**values, "identity_ref": config.identity_ref})
        self.deployment_private_key: pulumi.Output[str] = pulumi.Output.secret(
            deployment_key.private_key_openssh
        )
        self.register_outputs(
            {
                "connection_profile": self.connection_profile,
                "deployment_private_key": self.deployment_private_key,
                "storage_device": self.storage_device,
            }
        )
