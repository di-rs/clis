"""A gateway release must leave the proxy and other Compose projects alone."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_gateway_release_targets_only_gateway(tmp_path: Path) -> None:
    log = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nwith open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    docker.chmod(0o755)
    release = tmp_path / "release"
    release.mkdir()
    (release / "gateway.changed").touch()
    (release / "revision").write_text("a" * 40)
    (release / "image.env").write_text(
        "PLAYGROUND_IMAGE=ghcr.io/test/playground@sha256:" + "a" * 64 + "\n"
    )
    result = subprocess.run(
        ["bash", str(ROOT / "tools/cli-playground/infra/deploy.sh"), str(release)],
        env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"], "CALLS": str(log)},
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
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o755)
    digest = "ghcr.io/test/sandbox@sha256:" + "c" * 64
    result = subprocess.run(
        ["bash", str(ROOT / "tools/cli-playground/infra/deploy.sh"), str(release)],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "CLIS_CONFIG_DIR": str(config.parent),
            "CLIS_STATE_DIR": str(tmp_path / "state"),
            "PLAYGROUND_SANDBOX_IMAGE": digest,
            "IMAGE_LOG": str(tmp_path / "image"),
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "image").read_text() == image
    profile["playground"]["image_digest"] = digest
    assert json.loads((config / "profiles.json").read_text()) == profile
