"""A gateway release must leave the proxy and other Compose projects alone."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_gateway_release_targets_only_gateway(tmp_path: Path) -> None:
    log = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nwith open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    docker.chmod(0o755)
    config = tmp_path / "config"
    config.mkdir()
    (config / "playground.env").write_text("SETTING=value\n")
    release = tmp_path / "release"
    release.mkdir()
    (release / "gateway.changed").touch()
    (release / "revision").write_text("a" * 40)
    (release / "image.env").write_text(
        "PLAYGROUND_IMAGE=ghcr.io/test/playground@sha256:" + "a" * 64 + "\n"
    )
    environment = {
        **os.environ,
        "PATH": str(tmp_path) + ":" + os.environ["PATH"],
        "CALLS": str(log),
        "CLIS_CONFIG_DIR": str(config),
    }
    command = ["bash", str(ROOT / "tools/cli-playground/infra/deploy.sh")]
    preflight = subprocess.run(
        [*command, "preflight", str(release)], env=environment, capture_output=True, text=True
    )
    assert preflight.returncode == 0, preflight.stderr
    assert not any(
        "up" in call for call in [json.loads(line) for line in log.read_text().splitlines()]
    )
    result = subprocess.run(
        [*command, "apply", str(release)], env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert (release / "playground.env").read_text() == "SETTING=value\n"
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    updates = [call for call in calls if "up" in call]
    assert len(updates) == 1
    assert updates[0][-1] == "gateway"
    assert "--no-deps" in updates[0]
    assert not any("proxy" in call or "down" in call for call in calls)


def test_sandbox_update_preserves_boxie_image_and_custom_profile(tmp_path: Path) -> None:
    import shutil

    release = tmp_path / "release"
    owner = release / "tools/cli-playground/infra/playground_infra"
    owner.mkdir(parents=True)
    shutil.copy(ROOT / "tools/cli-playground/infra/playground_infra/installation.py", owner)
    (release / "revision").write_text("a" * 40)
    (release / "sandbox.changed").touch()
    (release / "expected-boxie-revision").write_text("b" * 40)
    config = tmp_path / "config/boxie"
    config.mkdir(parents=True)
    profile = {
        "playground": {"image_digest": "old", "commands": [["custom"]], "env": {"TERM": ["custom"]}}
    }
    (config / "profiles.json").write_text(json.dumps(profile))
    current = tmp_path / "state/boxie/current"
    deploy = current / "tools/boxie/infra/deploy.sh"
    deploy.parent.mkdir(parents=True)
    deploy.write_text('printf "%s" "$BOXIE_RUNTIME_IMAGE" > "$IMAGE_LOG"\n')
    image = "ghcr.io/test/boxie@sha256:" + "b" * 64
    (current / "image.env").write_text("BOXIE_RUNTIME_IMAGE=" + image + "\n")
    (current / "revision").write_text("b" * 40)
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n')
    docker.chmod(0o755)
    gateway = tmp_path / "state/playground/current"
    gateway.mkdir(parents=True)
    (gateway / "playground.env").write_text("SETTING=saved\n")
    (config.parent / "playground.env").write_text("SETTING=changed\n")
    (release / "image.env").write_text(
        "PLAYGROUND_IMAGE=ghcr.io/test/gateway@sha256:" + "d" * 64 + "\n"
    )
    digest = "ghcr.io/test/sandbox@sha256:" + "c" * 64
    command = ["bash", str(ROOT / "tools/cli-playground/infra/deploy.sh")]
    environment = {
        **os.environ,
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        "PATH": str(tmp_path) + ":" + os.environ["PATH"],
        "CLIS_CONFIG_DIR": str(config.parent),
        "CLIS_STATE_DIR": str(tmp_path / "state"),
        "PLAYGROUND_SANDBOX_IMAGE": digest,
        "IMAGE_LOG": str(tmp_path / "image"),
    }
    preflight = subprocess.run(
        [*command, "preflight", str(release)], env=environment, text=True, capture_output=True
    )
    assert preflight.returncode == 0, preflight.stderr
    assert json.loads((config / "profiles.json").read_text()) == profile
    result = subprocess.run(
        [*command, "apply", str(release)], env=environment, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "image").read_text() == image
    profile["playground"]["image_digest"] = digest
    assert json.loads((config / "profiles.json").read_text()) == profile

    assert (release / "playground.env").read_text() == "SETTING=saved\n"
    assert "compose" not in (tmp_path / "docker.log").read_text()
    (release / "recovery").touch()
    result = subprocess.run(
        [*command, "apply", str(release)], env=environment, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert "up -d" in (tmp_path / "docker.log").read_text()
    assert (release / "playground.env").read_text() == "SETTING=saved\n"


def test_readiness_waits_for_public_tls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from unittest.mock import MagicMock
    from urllib.error import URLError

    credential = tmp_path / "credential"
    credential.write_text("token")
    monkeypatch.setenv("PLAYGROUND_BOXIE_CREDENTIAL_FILE", str(credential))
    monkeypatch.setenv("PLAYGROUND_BOXIE_URL", "http://boxie")
    monkeypatch.setenv("PLAYGROUND_PUBLIC_URL", "https://playground")
    response = MagicMock()
    response.__enter__.return_value.status = 200
    request = MagicMock(side_effect=[response, URLError("certificate pending"), response, response])
    monkeypatch.setattr("urllib.request.urlopen", request)
    monkeypatch.setattr("time.sleep", lambda _: None)
    script = (
        (ROOT / "tools/cli-playground/infra/deploy.sh")
        .read_text()
        .split("<<'PY'\n")[1]
        .split("\nPY")[0]
    )
    exec(script, {})
    assert request.call_count == 4


def test_readiness_deadline_reports_public_https(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import MagicMock
    from urllib.error import URLError

    credential = tmp_path / "credential"
    credential.write_text("token")
    monkeypatch.setenv("PLAYGROUND_BOXIE_CREDENTIAL_FILE", str(credential))
    monkeypatch.setenv("PLAYGROUND_BOXIE_URL", "http://boxie")
    monkeypatch.setenv("PLAYGROUND_PUBLIC_URL", "https://playground")
    response = MagicMock()
    response.__enter__.return_value.status = 200
    monkeypatch.setattr(
        "urllib.request.urlopen", MagicMock(side_effect=[response, URLError("certificate pending")])
    )
    monkeypatch.setattr("time.monotonic", MagicMock(side_effect=[0, 301]))
    script = (
        (ROOT / "tools/cli-playground/infra/deploy.sh")
        .read_text()
        .split("<<'PY'\n")[1]
        .split("\nPY")[0]
    )
    with pytest.raises(SystemExit, match="public HTTPS readiness failed after 300 seconds"):
        exec(script, {})
