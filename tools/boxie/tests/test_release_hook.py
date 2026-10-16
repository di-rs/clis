"""A runtime update stops admission before ending orchestrator-owned terminals."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "infra/pre-activate.sh"


@pytest.mark.parametrize("fail", ["", "api", "worker", "agent"])
def test_update_stops_in_order_and_propagates_failure(tmp_path: Path, fail: str) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "calls.jsonl"
    docker.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        "with open(os.environ['CALLS'],'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "sys.exit(1 if sys.argv[-1] == os.environ['FAIL'] else 0)\n"
    )
    docker.chmod(0o700)
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "CLIS_PREVIOUS_RELEASE_DIR": "/opt/clis/boxie/releases/previous",
            "CLIS_RELEASE_DIR": "/opt/clis/boxie/releases/next",
            "CLIS_COMPOSE_PROJECT": "clis-boxie",
            "CLIS_COMPOSE_FILE": "/opt/clis/boxie/releases/next/compose.production.yml",
            "CALLS": str(log),
            "FAIL": fail,
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == (not fail), result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    expected = ["api", "worker", "agent"]
    if fail:
        expected = expected[: expected.index(fail) + 1]
    assert [call[-1] for call in calls] == expected
    for call in calls:
        assert call[:3] == ["compose", "--env-file", "/dev/null"]
        assert call[call.index("-p") + 1] == "clis-boxie"
        assert "stop" in call
        assert not {"down", "rm", "prune", "-v"}.intersection(call)


def test_first_install_does_not_stop_services(tmp_path: Path) -> None:
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        env={**os.environ, "CLIS_PREVIOUS_RELEASE_DIR": ""},
        capture_output=True,
    )
    assert result.returncode == 0
