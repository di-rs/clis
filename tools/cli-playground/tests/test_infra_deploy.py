"""Retained deployment helper contracts; activation itself is deliberately guarded."""

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from playground_infra import deploy

from .helpers import APP_ENV, SANDBOX_IMAGE, git_commit, protected


def test_explicit_ci_ssh_identity_pins_host_without_ambient_configuration(tmp_path: Path) -> None:
    command = deploy.ssh_command("192.0.2.1", tmp_path / "key", tmp_path / "known_hosts")
    assert command[-2:] == ["--", "root@192.0.2.1"]
    assert "StrictHostKeyChecking=yes" in command
    assert "IdentitiesOnly=yes" in command
    assert command[command.index("-F") + 1] == "/dev/null"
    assert command[command.index("-i") + 1] == str(tmp_path / "key")
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in command


def test_deploy_refuses_unsafe_hosts_before_reading_secrets() -> None:
    with pytest.raises(ValueError, match="host"):
        deploy.validate_host("example.org;true")


def test_deploy_requires_private_file_permissions(tmp_path: Path) -> None:
    app = protected(tmp_path / "app.env", APP_ENV)
    app.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        deploy.environment_file(app)


def test_deploy_refuses_symlinked_secret_files(tmp_path: Path) -> None:
    source = protected(tmp_path / "source.env", APP_ENV)
    app = tmp_path / "app.env"
    app.symlink_to(source)
    with pytest.raises((ValueError, OSError)):
        deploy.AppConfig.model_validate(deploy.environment_file(app)[1])


@pytest.mark.parametrize(
    "data",
    [
        APP_ENV.replace(b"local-test-secret", b"${TOKEN}"),
        APP_ENV.replace(b"private-renewal-token", b"${TOKEN}"),
        APP_ENV.replace(b"CLOUDFLARE_DNS_API_TOKEN=private-renewal-token\n", b""),
        APP_ENV + b"UNKNOWN_FIELD=extra-value\n",
    ],
)
def test_invalid_app_configuration_is_rejected_before_network(tmp_path: Path, data: bytes) -> None:
    app = protected(tmp_path / "app.env", data)
    with pytest.raises(ValueError):
        deploy.AppConfig.model_validate(deploy.environment_file(app)[1])


def test_oversized_manifest_is_refused_before_ssh(tmp_path: Path) -> None:
    release = tmp_path / "release.json"
    release.write_bytes(b" " * 65536)
    with pytest.raises(ValueError, match="release"):
        deploy.read_release(release)


def test_tracked_secret_file_is_refused_before_archive_or_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Git is exercised for real, isolated from hook variables and user configuration.
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tracked = protected(tmp_path / "private.env", APP_ENV)
    subprocess.run(["git", "-C", str(tmp_path), "add", "private.env"], check=True)
    git_commit(tmp_path)
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="tracked"):
        deploy.committed_revision((tracked,))


@pytest.mark.parametrize(
    "responses,succeeds",
    [
        ([b'{"status":"starting"}', b'{"status":"ok"}'], True),
        ([b'{"status":"starting"}'] * 2, False),
    ],
)
def test_public_health_retries_until_ready_or_timeout(
    responses: list[bytes], succeeds: bool
) -> None:
    elapsed = [0.0]

    class Opener:
        def open(self, url: str, *, timeout: float) -> io.BytesIO:
            assert url == "https://play.example/healthz" and 0 < timeout <= 5
            return io.BytesIO(responses.pop(0))

    def sleep(seconds: float) -> None:
        elapsed[0] += seconds

    if succeeds:
        deploy.wait_for_health(
            "play.example", timeout=10, opener=Opener(), clock=lambda: elapsed[0], sleep=sleep
        )
    else:
        with pytest.raises(RuntimeError, match="Services started.*HTTPS health"):
            deploy.wait_for_health(
                "play.example", timeout=10, opener=Opener(), clock=lambda: elapsed[0], sleep=sleep
            )


@pytest.mark.parametrize("image", ["ghcr.io/team/app:latest", "app@sha256:" + "1" * 64])
def test_retained_release_parser_requires_registry_qualified_digests(
    tmp_path: Path, image: str
) -> None:
    path = tmp_path / "release.json"
    path.write_text(
        json.dumps({"revision": "a" * 40, "gateway_image": image, "sandbox_image": SANDBOX_IMAGE})
    )
    with pytest.raises(ValueError, match="image"):
        deploy.read_release(path)
