"""Every obsolete activation entrypoint fails before reading credentials or mutating state."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from playground_infra import ci, deploy
from playground_infra.config import ACTIVATION_UNAVAILABLE, ActivationUnavailable


@pytest.mark.parametrize("entry", ["ci", "deploy"])
def test_direct_activation_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entry: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("activation performed IO before its guard")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(ci, "required", forbidden)
    monkeypatch.setattr(ci, "stack_outputs", forbidden)
    monkeypatch.setattr(deploy, "validate_host", forbidden)
    with pytest.raises(ActivationUnavailable, match="Production activation is unavailable"):
        if entry == "ci":
            ci.deploy_runtime()
        else:
            deploy.deploy("invalid;host", tmp_path / "missing-secret", tmp_path / "missing-release")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("entry", ["ci", "deploy", "shell"])
def test_activation_cli_fails_without_reading_secrets_or_running_tools(
    tmp_path: Path, entry: str
) -> None:
    sentinel = tmp_path / "executed"
    for name in ("docker", "pulumi", "ssh", "install", "mv"):
        binary = tmp_path / name
        binary.write_text(f'#!/bin/sh\ntouch "{sentinel}"\n')
        binary.chmod(0o755)
    existing = tmp_path / "app.env"
    existing.write_text("private-sentinel-do-not-print")
    release = tmp_path / "release.json"
    release.write_text("previous-release")
    if entry == "shell":
        command = ["/bin/bash", str(Path(deploy.__file__).parent.parent / "deploy.sh")]
    elif entry == "ci":
        command = [sys.executable, "-m", "playground_infra.ci", "deploy"]
    else:
        command = [
            sys.executable,
            "-m",
            "playground_infra.deploy",
            "--host",
            "invalid;host",
            "--env-file",
            str(existing),
            "--release-file",
            str(release),
        ]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path), "GHCR_TOKEN": "private-sentinel-do-not-print"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == (2 if entry == "shell" else 1)
    assert result.stdout == ""
    assert result.stderr == ("" if entry == "shell" else ACTIVATION_UNAVAILABLE + "\n")
    assert not sentinel.exists()
    assert existing.read_text() == "private-sentinel-do-not-print"
    assert release.read_text() == "previous-release"
