"""Playground backend and sandbox updates at the command boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_backend_release_targets_only_backend(tmp_path: Path) -> None:
    log = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nwith open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    docker.chmod(0o755)
    config = tmp_path / "config"
    config.mkdir()
    (config / "cli-playground.env").write_text("SETTING=value\n")
    image = config / "cli-playground-image.env"
    image.write_text("CLI_PLAYGROUND_IMAGE=ghcr.io/test/backend@sha256:" + "a" * 64 + "\n")
    owner = tmp_path / "owner"
    owner.mkdir()
    result = subprocess.run(
        ["bash", str(ROOT / "tools/cli-playground/infra/deploy.sh")],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "CALLS": str(log),
            "SANCTUARY_CONFIG_DIR": str(config),
            "SANCTUARY_CLI_PLAYGROUND_DIR": str(owner),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    updates = [call for call in calls if "up" in call]
    assert len(updates) == 1
    assert updates[0][-1] == "backend"
    assert "--no-deps" in updates[0]
    assert not any("proxy" in call or "down" in call for call in calls)


def test_readiness_waits_for_public_tls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from unittest.mock import MagicMock
    from urllib.error import URLError

    credential = tmp_path / "credential"
    credential.write_text("token")
    monkeypatch.setenv("CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE", str(credential))
    monkeypatch.setenv("CLI_PLAYGROUND_BOXIE_URL", "http://boxie")
    monkeypatch.setenv("CLI_PLAYGROUND_PUBLIC_URL", "https://playground")
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
    monkeypatch.setenv("CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE", str(credential))
    monkeypatch.setenv("CLI_PLAYGROUND_BOXIE_URL", "http://boxie")
    monkeypatch.setenv("CLI_PLAYGROUND_PUBLIC_URL", "https://playground")
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
