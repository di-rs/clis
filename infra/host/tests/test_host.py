"""Provisioning contracts at the Pulumi provider boundary, with no cloud calls."""

import importlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Never

import pulumi
import pytest
from pulumi.runtime.settings import reset_options

ROOT = Path(__file__).parents[1]
INSTALLATION = "12345678-1234-4234-8234-123456789abc"
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
POLICY = "[Journal]\nStorage=persistent\nSystemMaxUse=1G\nRuntimeMaxUse=128M\nSystemKeepFree=4G\nMaxRetentionSec=2day\nMaxFileSec=1h\n"


def api() -> Any:
    assert importlib.util.find_spec("host_infra"), "shared platform package is missing"
    return importlib.import_module("host_infra.host")


def test_generic_host_needs_no_product_or_cloudflare_configuration() -> None:
    host = api().HostConfig(installation_id=INSTALLATION, identity_ref="BOXIE_DEPLOYMENT_KEY")
    assert host.ssh_cidrs == ["0.0.0.0/0"]
    assert host.volume_size_gb >= 20


@pytest.mark.parametrize(
    "values",
    [
        {"ssh_cidrs": []},
        {"ssh_cidrs": ["192.0.2.1"]},
        {"ssh_cidrs": ["::/0"]},
        {"ssh_public_key": "ssh-ed25519 invalid@base64"},
        {"identity_ref": "/tmp/private-key"},
        {"installation_id": "bad"},
        {"volume_size_gb": 0},
    ],
)
def test_invalid_host_inputs_are_rejected(values: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        api().HostConfig(
            **{"installation_id": INSTALLATION, "identity_ref": "DEPLOYMENT_KEY", **values}
        )


def test_storage_guard_accepts_only_the_expected_mounted_device(tmp_path: Path) -> None:
    guard = ROOT / "storage-guard.sh"
    assert guard.is_file(), "persistent storage guard is missing"
    mounted = tmp_path / "data"
    mounted.mkdir()
    expected = tmp_path / "volume"
    expected.touch()
    wrong = tmp_path / "root-disk"
    wrong.touch()
    commands = tmp_path / "bin"
    commands.mkdir()
    findmnt = commands / "findmnt"
    findmnt.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_SOURCE"\n')
    findmnt.chmod(0o755)
    environment = {**os.environ, "PATH": str(commands) + ":" + os.environ["PATH"]}
    for source, success in [(str(expected), True), (str(wrong), False), ("", False)]:
        result = subprocess.run(
            ["bash", str(guard), str(mounted), str(expected)],
            env={**environment, "TEST_SOURCE": source},
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) is success, result.stderr


class HostMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.inputs: dict[tuple[str, str], dict[str, Any]] = {}
        self.resources: list[pulumi.ResourceTransformationArgs] = []
        reset_options()
        pulumi.runtime.set_mocks(self, project="single-host", stack="test")
        pulumi.runtime.register_stack_transformation(self.observe)

    def observe(self, args: pulumi.ResourceTransformationArgs) -> None:
        self.resources.append(args)

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.inputs[args.typ, args.name] = args.inputs
        return "42", {
            **args.inputs,
            "ipAddress": "192.0.2.10",
            "publicKeyOpenssh": PUBLIC_KEY + "\n",
            "privateKeyOpenssh": "fixture-private-key",
        }

    def call(self, args: pulumi.runtime.MockCallArgs) -> Never:
        raise AssertionError(f"unexpected provider lookup: {args.token}")


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_host_pins_identity_protects_data_and_opens_only_ssh_by_default() -> None:
    mocks = HostMocks()
    host = api().Host(
        "host",
        api().HostConfig(installation_id=INSTALLATION, identity_ref="DEPLOYMENT_KEY"),
        journal_policy=POLICY,
    )
    profile = await host.connection_profile.future()
    for item in mocks.resources:
        await item.resource.urn.future()
    assert profile == {
        "host": "192.0.2.10",
        "host_public_key": PUBLIC_KEY,
        "identity_ref": "DEPLOYMENT_KEY",
    }
    assert not await host.connection_profile.is_secret()
    assert await host.deployment_private_key.is_secret()
    assert "fixture-private-key" not in json.dumps(profile)
    server = next(
        v for (kind, _), v in mocks.inputs.items() if kind == "hcloud:index/server:Server"
    )
    firewall = next(
        v for (kind, _), v in mocks.inputs.items() if kind == "hcloud:index/firewall:Firewall"
    )
    assert {r.get("port") for r in firewall["rules"] if r["protocol"] == "tcp"} == {"22"}
    assert server["firewallIds"] == [42]
    volume = next(
        v for (kind, _), v in mocks.inputs.items() if kind == "hcloud:index/volume:Volume"
    )
    assert volume["format"] == "ext4" and volume["automount"] is False
    protected = {item.type_: item.opts.protect for item in mocks.resources}
    assert protected["hcloud:index/volume:Volume"]
    assert protected["hcloud:index/server:Server"]
    assert not any("docker" in kind.lower() or "dnsRecord" in kind for kind, _ in mocks.inputs)
    assert await host.storage_device.future() == "/dev/disk/by-id/scsi-0HC_Volume_42"
    raw_user_data = await host.server.user_data.future()
    assert raw_user_data is not None
    user_data = json.loads(raw_user_data.removeprefix("#cloud-config\n"))
    files = {entry["path"]: entry["content"] for entry in user_data["write_files"]}
    assert json.loads(files["/etc/docker/daemon.json"])["data-root"] == "/srv/boxie/docker"
    assert (
        "RequiresMountsFor=/srv/boxie" in files["/etc/systemd/system/docker.service.d/storage.conf"]
    )


def test_cloud_init_installs_the_explicit_bounded_journal_policy() -> None:
    policy = (
        "[Journal]\nStorage=persistent\nSystemMaxUse=1G\nRuntimeMaxUse=128M\n"
        "SystemKeepFree=4G\nMaxRetentionSec=2day\nMaxFileSec=1h\n"
    )
    value = json.loads(
        api()
        .cloud_init(
            "private-fixture",
            PUBLIC_KEY,
            "/dev/disk/by-id/scsi-0HC_Volume_42",
            journal_policy=policy,
        )
        .removeprefix("#cloud-config\n")
    )
    files = {entry["path"]: entry for entry in value["write_files"]}
    assert files["/etc/systemd/journald.conf.d/60-clis.conf"] == {
        "path": "/etc/systemd/journald.conf.d/60-clis.conf",
        "permissions": "0644",
        "content": policy,
    }
    assert "/usr/local/sbin/boxie-journal-setup" in files


def test_journal_setup_prepares_read_access_and_bounded_activation(tmp_path: Path) -> None:
    script = ROOT / "journal-setup.sh"
    assert script.is_file(), "persistent journal activation is missing"
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "commands.jsonl"
    source = """#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ["COMMAND_LOG"], "a") as target:
    target.write(json.dumps([name, *sys.argv[1:]]) + "\\n")
if name == "getent":
    print("systemd-journal:x:190:")
elif name == "cat":
    print("0123456789abcdef0123456789abcdef")
"""
    for name in ["timeout", "install", "getent", "cat", "chmod", "setfacl"]:
        path = commands / name
        path.write_text(source)
        path.chmod(0o755)
    (tmp_path / "etc/boxie-platform").mkdir(parents=True)
    policy_path = tmp_path / "etc/systemd/journald.conf.d/60-clis.conf"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(POLICY)
    subprocess.run(
        ["bash", str(script), str(tmp_path)],
        env={
            **os.environ,
            "PATH": str(commands) + ":" + os.environ["PATH"],
            "COMMAND_LOG": str(log),
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert ["timeout", "60", "systemd-tmpfiles", "--create", "--prefix=/var/log/journal"] in calls
    assert ["timeout", "60", "systemctl", "restart", "systemd-journald.service"] in calls
    assert ["timeout", "60", "journalctl", "--flush"] in calls
    assert any("g:systemd-journal:rX" in call for call in calls)
    assert (tmp_path / "etc/boxie-platform/journal-group-gid").read_text() == "190\n"


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_host_rejects_unbounded_journal_policy_before_resource_registration() -> None:
    mocks = HostMocks()
    with pytest.raises(ValueError, match="bounded journal policy"):
        api().Host(
            "host",
            api().HostConfig(installation_id=INSTALLATION, identity_ref="DEPLOYMENT_KEY"),
            journal_policy="[Journal]\nStorage=persistent\n",
        )
    assert not mocks.resources
