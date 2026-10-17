"""Boxie release ordering at the Docker command boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("failure", ["pull", "migrate", ""])
def test_boxie_release_pulls_before_stopping_and_keeps_dependencies_running(
    tmp_path: Path, failure: str
) -> None:
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "with open(os.environ['CALLS'], 'a') as output: output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if os.environ['FAILURE'] == 'pull' and 'pull' in sys.argv: sys.exit(17)\n"
        "if os.environ['FAILURE'] == 'migrate' and 'run' in sys.argv and 'migrate' in sys.argv: sys.exit(17)\n"
    )
    docker.chmod(0o755)
    config = tmp_path / "config"
    (config / "boxie").mkdir(parents=True)
    (config / "boxie.env").write_text("")
    (config / "boxie/api.json").write_text('{"release":"old"}')
    (config / "boxie/orchestrator.json").write_text("{}")
    for name in ("installation-id", "host-id"):
        (config / "boxie" / name).write_text("11111111-1111-1111-1111-111111111111")
    image = tmp_path / "boxie-image.env"
    image.write_text("BOXIE_RUNTIME_IMAGE=ghcr.io/test/boxie@sha256:" + "a" * 64 + "\n")
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "api.json").write_text('{"release":"old"}')
    mounted = tmp_path / "mounted-api.json"
    os.link(owner / "api.json", mounted)
    log = tmp_path / "calls"
    result = subprocess.run(
        ["bash", str(ROOT / "tools/boxie/infra/deploy.sh"), "a" * 40],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "CALLS": str(log),
            "FAILURE": failure,
            "CLIS_CONFIG_DIR": str(config),
            "CLIS_BOXIE_DIR": str(owner),
            "CLIS_BOXIE_IMAGE_FILE": str(image),
        },
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert result.returncode == (17 if failure else 0), result.stderr
    assert json.loads(mounted.read_text())["release"] == "old"
    pulls = [i for i, call in enumerate(calls) if "pull" in call]
    stops = [i for i, call in enumerate(calls) if "stop" in call]
    starts = [call for call in calls if "up" in call]
    assert pulls
    assert bool(stops) == (failure != "pull")
    if stops:
        assert pulls[0] < stops[0]
        assert [call[-1] for call in calls if "stop" in call] == ["api", "orchestrator"]
    assert bool(starts) == (failure == "")
    if starts:
        assert [call[-1] for call in starts] == ["orchestrator", "api"]
        assert all("compose.observability.yml" in " ".join(call) for call in starts)
    assert not any("down" in call or "postgres" in call or "temporal" in call for call in calls)
    if not failure:
        assert json.loads((owner / "api.json").read_text())["release"] == "a" * 40
