"""Shared host resources and fail-closed storage boundary."""

import json
import subprocess
from pathlib import Path
from typing import Any, Never

import pulumi
from pulumi.runtime.settings import get_root_resource, reset_options
from pulumi.runtime.stack import Stack


class HostMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.inputs: dict[tuple[str, str], dict[str, Any]] = {}
        self.registrations: dict[tuple[str, str], pulumi.ResourceTransformationArgs] = {}
        reset_options()
        pulumi.runtime.set_mocks(self, project="clis", stack="test")
        pulumi.runtime.register_stack_transformation(self.observe)

    def observe(self, args: pulumi.ResourceTransformationArgs) -> None:
        self.registrations[args.type_, args.name] = args

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.inputs[args.typ, args.name] = args.inputs
        return "123", {
            **args.inputs,
            "ipAddress": "203.0.113.12",
            "aud": "test-audience",
            "stdout": "",
            "stderr": "",
            "privateKeyOpenssh": "private",
            "publicKeyOpenssh": "ssh-ed25519 YWJj",
        }

    def call(self, args: pulumi.runtime.MockCallArgs) -> Never:
        raise AssertionError(args.token)

    async def finish(self) -> None:
        for registration in list(self.registrations.values()):
            await registration.resource.urn.future()


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
    firewall = mocks.inputs["hcloud:index/firewall:Firewall", "clis"]
    assert {rule.get("port") for rule in firewall["rules"] if rule["protocol"] == "tcp"} == {
        "22",
        "80",
        "443",
    }
    user_data = await server.user_data.future()
    assert user_data is not None
    data = json.loads(user_data.removeprefix("#cloud-config\n"))
    assert any(file["path"] == "/etc/clis/storage-device" for file in data["write_files"])
    root = get_root_resource()
    assert isinstance(root, Stack)
    assert await pulumi.Output.from_input(root.outputs["sshPrivateKey"]).is_secret()


def test_storage_guard_refuses_missing_disk(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "infra/host/storage-guard.sh"),
            str(tmp_path),
            "/dev/missing-clis-volume",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "persistent disk" in result.stderr


def test_cloud_init_installs_bounded_operation_units() -> None:
    from host_infra.host import cloud_init

    data = json.loads(cloud_init("private", "public", "123").removeprefix("#cloud-config\n"))
    files = {item["path"]: item["content"] for item in data["write_files"]}
    assert "/usr/local/sbin/clis-operation" in files
    for app in ("setup", "boxie", "playground"):
        unit = files[f"/etc/systemd/system/clis-{app}@.service"]
        assert "TimeoutStartSec=900" in unit
        assert "KillMode=control-group" in unit
        assert "RemainAfterExit=yes" in unit
        assert "/usr/bin/flock -n /run/lock/clis-deployment.lock" in unit


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_host_exposes_pinned_setup_connection_and_attachment() -> None:
    from host_infra.host import HostConfig, provision_resources

    mocks = HostMocks()
    host = provision_resources(HostConfig())
    await mocks.finish()
    assert await pulumi.Output.from_input(host.connection.host_key).future() == "ssh-ed25519 YWJj"
    assert await pulumi.Output.from_input(host.connection.private_key).is_secret()
    assert await host.volume_attachment.urn.future()
