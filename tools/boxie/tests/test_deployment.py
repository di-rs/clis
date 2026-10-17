"""Production deployment sequencing at the Docker command boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("failure", ["pull", "migrate", "ready", "smoke", ""])
def test_boxie_deployment_stops_only_after_pull_and_leaves_failed_migration_stopped(
    tmp_path: Path, failure: str
) -> None:
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "calls"
    docker = commands / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if os.environ['FAILURE'] == 'pull' and 'pull' in sys.argv: sys.exit(17)\n"
        "if os.environ['FAILURE'] == 'migrate' and 'run' in sys.argv and 'migrate' in sys.argv: sys.exit(17)\n"
    )
    with docker.open("a") as output:
        output.write("if os.environ['FAILURE'] == 'ready' and 'exec' in sys.argv: sys.exit(17)\n")
        output.write(
            "if os.environ['FAILURE'] == 'smoke' and 'run' in sys.argv and 'smoke' in sys.argv: sys.exit(17)\n"
        )
    docker.chmod(0o755)
    config = tmp_path / "config"
    (config / "boxie").mkdir(parents=True)
    (config / "boxie.env").write_text("")
    (config / "boxie/api.json").write_text('{"release":"local"}')
    (config / "boxie/orchestrator.json").write_text("{}")
    for name in ("installation-id", "host-id"):
        (config / "boxie" / name).write_text("11111111-1111-1111-1111-111111111111")
    release = tmp_path / "release"
    release.mkdir()
    (release / "image.env").write_text(
        "BOXIE_RUNTIME_IMAGE=ghcr.io/test/boxie@sha256:" + "a" * 64 + "\n"
    )
    (release / "revision").write_text("a" * 40 + "\n")
    command = ["bash", str(ROOT / "tools/boxie/infra/deploy.sh")]
    environment = {
        **os.environ,
        "PATH": str(commands) + ":" + os.environ["PATH"],
        "CALLS": str(log),
        "FAILURE": failure,
        "CLIS_CONFIG_DIR": str(config),
    }
    preflight = subprocess.run(
        [*command, "preflight", str(release)],
        env=environment,
        capture_output=True,
        text=True,
    )
    if failure == "pull":
        result = preflight
    else:
        assert preflight.returncode == 0, preflight.stderr
        assert not any(
            "stop" in call for call in [json.loads(line) for line in log.read_text().splitlines()]
        )
        result = subprocess.run(
            [*command, "apply", str(release)], env=environment, capture_output=True, text=True
        )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    if failure:
        assert result.returncode == 17, result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads((release / "api.json").read_text())["release"] == "a" * 40
        assert (release / "boxie.env").read_text() == ""
        assert (release / "orchestrator.json").read_text() == "{}"
        saved = (release / "api.json").read_bytes()
        (config / "boxie/api.json").write_text('{"release":"changed","unrelated":"setting"}')
        repeated = subprocess.run(
            [*command, "apply", str(release)],
            env={**environment, "FAILURE": ""},
            capture_output=True,
            text=True,
        )
        assert repeated.returncode == 0, repeated.stderr
        assert (release / "api.json").read_bytes() == saved

    stopped = [call for call in calls if "stop" in call]
    started = [call for call in calls if "up" in call]
    assert bool(stopped) == (failure != "pull")
    assert bool(started) == (failure not in {"pull", "migrate"})
    if started:
        assert [call[-1] for call in started] == ["orchestrator", "api"]
        assert "--no-deps" in started[0]
    assert not any("down" in call for call in calls)
    assert any("run" in call and "smoke" in call for call in calls) == (failure in {"", "smoke"})
