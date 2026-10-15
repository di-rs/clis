"""CI configuration and deployment through process and private-file boundaries."""

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from playground_infra import ci

from .helpers import GATEWAY_IMAGE, SANDBOX_IMAGE

OUTPUTS = {
    "ipv4": "203.0.113.12",
    "publicUrl": "https://play.example.com",
    "sshPrivateKey": "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-key\n-----END OPENSSH PRIVATE KEY-----\n",
    "sshHostPublicKey": "ssh-ed25519 YWJj\n",
}


@pytest.fixture
def ci_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "CLOUDFLARE_ZONE_ID": "b" * 32,
        "PULUMI_BACKEND_URL": "s3://state?endpoint=account.eu.r2.cloudflarestorage.com&region=auto&awssdk=v2&s3ForcePathStyle=true",
        "PLAYGROUND_URL": "https://play.example.com",
        "ACME_EMAIL": "admin@example.com",
        "PLAYGROUND_DEPLOY_TOKEN": "test-deployment-token",
        "CLOUDFLARE_DNS_API_TOKEN": "private-renewal-token",
        "PULUMI_CONFIG_PASSPHRASE": "private-state-passphrase",
        "RELEASE_REVISION": "c" * 40,
        "GATEWAY_IMAGE": GATEWAY_IMAGE,
        "SANDBOX_IMAGE": SANDBOX_IMAGE,
        "GITHUB_SHA": "c" * 40,
        "GITHUB_ACTOR": "ci-user",
        "GHCR_TOKEN": "private-registry-token",
    }.items():
        monkeypatch.setenv(key, value)


def configure_cli(path: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(Path(ci.__file__)), "configure", "--config-file", str(path)],
        env=environment,
        capture_output=True,
        text=True,
    )


def test_configure_cli_preserves_reviewed_stack_settings(
    tmp_path: Path, ci_environment: None
) -> None:
    path = tmp_path / "Pulumi.production.yaml"
    path.write_text("config:\n  cli-playground:serverType: cx43\n  cli-playground:protect: true\n")
    result = configure_cli(path, dict(os.environ))
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert yaml.safe_load(path.read_text())["config"] == {
        "cli-playground:serverType": "cx43",
        "cli-playground:protect": True,
        "cli-playground:domain": "play.example.com",
        "cli-playground:cloudflareZoneId": "b" * 32,
    }
    assert "private-state-passphrase" not in path.read_text()
    assert "test-deployment-token" not in path.read_text()
    assert "private-renewal-token" not in path.read_text()


def test_configure_does_not_require_runtime_credentials(
    tmp_path: Path, ci_environment: None
) -> None:
    environment = dict(os.environ)
    for name in ("CLOUDFLARE_DNS_API_TOKEN", "PLAYGROUND_DEPLOY_TOKEN", "ACME_EMAIL"):
        environment.pop(name, None)
    path = tmp_path / "stack.yaml"
    result = configure_cli(path, environment)
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(path.read_text())["config"]["cli-playground:domain"] == "play.example.com"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PULUMI_CONFIG_PASSPHRASE", None),
        ("PULUMI_CONFIG_PASSPHRASE", ""),
        ("PULUMI_BACKEND_URL", ""),
        ("CLOUDFLARE_ZONE_ID", "invalid-zone"),
        ("PLAYGROUND_URL", "https://play.example.com/path"),
        ("PLAYGROUND_URL", "https://play.example.com/"),
        ("PLAYGROUND_URL", "http://play.example.com"),
        ("PLAYGROUND_URL", "https://play..example.com"),
    ],
)
def test_configure_cli_fails_before_writing_invalid_configuration(
    ci_environment: None, tmp_path: Path, key: str, value: str | None
) -> None:
    environment = dict(os.environ)
    environment.pop(key, None)
    if value is not None:
        environment[key] = value
    path = tmp_path / "stack.yaml"
    result = configure_cli(path, environment)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Playground CI configure failed (ValueError)" in result.stderr
    assert "do-not-print" not in result.stderr
    assert "private-state-passphrase" not in result.stderr
    assert not path.exists()


def test_prepare_pins_state_host_key_and_writes_only_private_files(
    tmp_path: Path, ci_environment: None
) -> None:
    directory = tmp_path / "private"
    assert ci.prepare(directory, OUTPUTS) == "203.0.113.12"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert (directory / "known_hosts").read_text() == "203.0.113.12 ssh-ed25519 YWJj\n"
    assert (directory / "identity").read_text() == OUTPUTS["sshPrivateKey"]
    assert "CLOUDFLARE_DNS_API_TOKEN=private-renewal-token\n" in (directory / "app.env").read_text()
    assert {p.name for p in directory.iterdir()} == {
        "identity",
        "known_hosts",
        "app.env",
        "release.json",
    }
    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("token", [None, "", "do-not-print\nINJECT=value", "${TOKEN}"])
def test_prepare_rejects_invalid_dns_credentials_before_private_writes(
    tmp_path: Path, ci_environment: None, monkeypatch: pytest.MonkeyPatch, token: str | None
) -> None:
    monkeypatch.delenv("CLOUDFLARE_DNS_API_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("CLOUDFLARE_DNS_API_TOKEN", token)
    directory = tmp_path / "private"
    with pytest.raises(ValueError) as error:
        ci.prepare(directory, OUTPUTS)
    assert "do-not-print" not in str(error.value)
    assert not directory.exists()


def test_release_from_another_commit_is_rejected_before_private_writes(
    tmp_path: Path, ci_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RELEASE_REVISION", "d" * 40)
    with pytest.raises(ValueError, match="revision"):
        ci.prepare(tmp_path / "private", {})
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("publicUrl", "https://other.example.com"),
        ("ipv4", "-oProxyCommand=bad"),
        ("sshHostPublicKey", "ssh-ed25519 YWJj\ninjected-key"),
        ("sshPrivateKey", "not-a-key"),
    ],
)
def test_invalid_state_is_rejected_before_private_writes(
    tmp_path: Path, ci_environment: None, key: str, value: str
) -> None:
    with pytest.raises(ValueError):
        ci.prepare(tmp_path / "private", {**OUTPUTS, key: value})
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize("success", [True, False])
def test_ssh_readiness_retries_with_pinned_identity_and_stops_at_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, success: bool
) -> None:
    elapsed = [0.0]
    calls: list[list[str]] = []

    def ssh(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0 if success and len(calls) == 2 else 255)

    monkeypatch.setattr(subprocess, "run", ssh)
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", lambda n: elapsed.__setitem__(0, elapsed[0] + n))
    if success:
        ci.wait_for_ssh("192.0.2.1", tmp_path, timeout=10)
    else:
        with pytest.raises(RuntimeError, match="pinned host"):
            ci.wait_for_ssh("192.0.2.1", tmp_path, timeout=10)
    assert len(calls) == 2
    assert all(command[-3:] == ["--", "root@192.0.2.1", "true"] for command in calls)
    assert all("StrictHostKeyChecking=yes" in command for command in calls)


def test_retained_state_reader_times_out_without_exposing_secret_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = tmp_path / "state-read-started"
    binary = tmp_path / "pulumi"
    binary.write_text(
        f"#!{sys.executable}\nimport pathlib, sys, time\n"
        f"pathlib.Path({str(started)!r}).touch()\n"
        "print('private-state-output', flush=True)\n"
        "time.sleep(2)\n"
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(ci, "STACK_OUTPUT_TIMEOUT", 0.5)
    with pytest.raises(subprocess.TimeoutExpired):
        ci.stack_outputs()
    assert started.exists()
