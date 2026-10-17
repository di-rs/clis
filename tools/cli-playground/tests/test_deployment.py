"""Playground gateway and sandbox updates at the command boundary."""

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
    image = config / "playground-image.env"
    image.write_text("PLAYGROUND_IMAGE=ghcr.io/test/gateway@sha256:" + "a" * 64 + "\n")
    owner = tmp_path / "owner"
    owner.mkdir()
    result = subprocess.run(
        ["bash", str(ROOT / "tools/cli-playground/infra/deploy.sh")],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "CALLS": str(log),
            "CLIS_CONFIG_DIR": str(config),
            "CLIS_PLAYGROUND_DIR": str(owner),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    updates = [call for call in calls if "up" in call]
    assert len(updates) == 1
    assert updates[0][-1] == "gateway"
    assert "--no-deps" in updates[0]
    assert not any("proxy" in call or "down" in call for call in calls)


def test_sandbox_update_preserves_boxie_image_and_custom_profile(tmp_path: Path) -> None:
    import shutil

    config = tmp_path / "config"
    boxie = config / "boxie"
    boxie.mkdir(parents=True)
    profile = {
        "playground": {"image_digest": "old", "commands": [["custom"]], "env": {"TERM": ["custom"]}}
    }
    (boxie / "profiles.json").write_text(json.dumps(profile))
    (config / "boxie.env").write_text("")
    boxie_image = "ghcr.io/test/boxie@sha256:" + "b" * 64
    (config / "boxie-image.env").write_text("BOXIE_RUNTIME_IMAGE=" + boxie_image + "\n")
    owner = tmp_path / "owner"
    (owner / "profiles").mkdir(parents=True)
    (owner / "playground_infra").mkdir()
    shutil.copy(
        ROOT / "tools/cli-playground/infra/playground_infra/installation.py",
        owner / "playground_infra/installation.py",
    )
    (owner / "profiles/playground.json").write_text(json.dumps(profile))
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nwith open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    docker.chmod(0o755)
    log = tmp_path / "calls"
    digest = "ghcr.io/test/sandbox@sha256:" + "c" * 64
    result = subprocess.run(
        ["bash", str(ROOT / "tools/cli-playground/infra/sandbox-release.sh"), digest],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "CALLS": str(log),
            "CLIS_CONFIG_DIR": str(config),
            "CLIS_PLAYGROUND_DIR": str(owner),
            "CLIS_BOXIE_DIR": str(tmp_path / "boxie-owner"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    profile["playground"]["image_digest"] = digest
    assert json.loads((boxie / "profiles.json").read_text()) == profile
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert any("pull" in call and digest in call for call in calls)
    stops = [index for index, call in enumerate(calls) if "stop" in call]
    updates = [call for call in calls if "up" in call]
    assert len(stops) == 1
    assert calls[stops[0]][-3:] == ["--timeout", "60", "orchestrator"]
    assert stops[0] < next(index for index, call in enumerate(calls) if "up" in call)
    assert len(updates) == 1
    assert updates[0][-1] == "orchestrator"
    assert "--force-recreate" in updates[0]
    assert not any("gateway" in call or "api" in call for call in calls)
    assert (config / "boxie-image.env").read_text() == "BOXIE_RUNTIME_IMAGE=" + boxie_image + "\n"


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
