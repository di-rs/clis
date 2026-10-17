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
