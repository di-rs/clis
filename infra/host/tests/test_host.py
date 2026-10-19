"""Shared host resources and fail-closed storage boundary."""

import json
import subprocess
from pathlib import Path

import pulumi
from host_mocks import HostMocks
from pulumi.runtime.settings import get_root_resource
from pulumi.runtime.stack import Stack

ROOT = Path(__file__).resolve().parents[3]


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_host_owns_protected_disk_and_no_product_dns() -> None:
    from host_infra.host import HostConfig, provision

    mocks = HostMocks()
    server = provision(HostConfig(ssh_cidrs=["203.0.113.1/32"], disk_size=80))
    await mocks.finish()
    await server.urn.future()
    volumes = [
        (name, value)
        for (kind, name), value in mocks.inputs.items()
        if kind == "hcloud:index/volume:Volume"
    ]
    assert len(volumes) == 1
    name, volume = volumes[0]
    assert volume["size"] == 80
    assert volume["deleteProtection"] is True
    assert mocks.registrations["hcloud:index/volume:Volume", name].opts.protect
    assert any(kind == "hcloud:index/volumeAttachment:VolumeAttachment" for kind, _ in mocks.inputs)
    assert not any(kind == "cloudflare:index/dnsRecord:DnsRecord" for kind, _ in mocks.inputs)
    server_options = mocks.registrations["hcloud:index/server:Server", "sanctuary"].opts
    assert server_options.protect
    assert server_options.additional_secret_outputs == ["userData"]
    assert server_options.ignore_changes == ["userData", "sshKeys"]
    for name in ("deployment-key", "host-key"):
        assert mocks.registrations["tls:index/privateKey:PrivateKey", name].opts.protect
    firewall = mocks.inputs["hcloud:index/firewall:Firewall", "sanctuary"]
    assert {rule.get("port") for rule in firewall["rules"] if rule["protocol"] == "tcp"} == {
        "22",
        "80",
        "443",
    }
    user_data = await server.user_data.future()
    assert user_data is not None
    data = json.loads(user_data.removeprefix("#cloud-config\n"))
    assert any(file["path"] == "/etc/sanctuary/storage-device" for file in data["write_files"])
    root = get_root_resource()
    assert isinstance(root, Stack)
    assert await pulumi.Output.from_input(root.outputs["sshPrivateKey"]).is_secret()


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_optional_admin_key_does_not_change_server_key_count() -> None:
    from host_infra.host import HostConfig, provision

    admin_key = "ssh-ed25519 YWJj operator@example.com"
    mocks = HostMocks()
    server = provision(HostConfig(ssh_public_key=admin_key))
    await mocks.finish()
    assert len(mocks.inputs["hcloud:index/server:Server", "sanctuary"]["sshKeys"]) == 1
    assert ("hcloud:index/sshKey:SshKey", "admin") not in mocks.inputs
    user_data = await server.user_data.future()
    assert user_data is not None
    data = json.loads(user_data.removeprefix("#cloud-config\n"))
    files = {item["path"]: item["content"] for item in data["write_files"]}
    assert files["/etc/sanctuary/admin-ssh-key"] == admin_key + "\n"
    assert data["runcmd"][:3] == [
        ["install", "-d", "-m", "0700", "/root/.ssh"],
        ["bash", "-c", "cat /etc/sanctuary/admin-ssh-key >> /root/.ssh/authorized_keys"],
        ["chmod", "0600", "/root/.ssh/authorized_keys"],
    ]


def test_storage_guard_refuses_missing_disk(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "infra/host/storage-guard.sh"),
            str(tmp_path),
            "/dev/missing-sanctuary-volume",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "persistent disk" in result.stderr


def test_cloud_init_contains_only_immutable_first_boot_files() -> None:
    from host_infra.host import cloud_init

    data = json.loads(cloud_init("private", "public", "123").removeprefix("#cloud-config\n"))
    files = {item["path"]: item["content"] for item in data["write_files"]}
    assert "/usr/local/sbin/sanctuary-operation" not in files
    assert not any(name.startswith("/etc/systemd/system/sanctuary-") for name in files)
    assert "/usr/local/sbin/sanctuary-bootstrap" in files
    assert "/usr/local/sbin/sanctuary-storage-guard" in files


def test_bootstrap_prepares_fixed_compose_directories() -> None:
    bootstrap = (ROOT / "infra/host/bootstrap.sh").read_text()
    assert "/opt/sanctuary/boxie /opt/sanctuary/cli-playground" in bootstrap
    assert "/opt/sanctuary/releases" not in bootstrap


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_host_exposes_private_ssh_output_and_attachment() -> None:
    from host_infra.host import HostConfig, provision_resources

    mocks = HostMocks()
    host = provision_resources(HostConfig())
    await mocks.finish()
    assert not hasattr(host, "connection")
    root = get_root_resource()
    assert isinstance(root, Stack)
    assert await pulumi.Output.from_input(root.outputs["sshPrivateKey"]).is_secret()
    assert await host.volume_attachment.urn.future()
